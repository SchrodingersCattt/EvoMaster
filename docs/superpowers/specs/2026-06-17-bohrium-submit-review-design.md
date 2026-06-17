# Bohrium submit 用户确认与参数审阅设计

> 日期：2026-06-17
> 状态：设计稿，待用户审阅。
> 范围：在 `Bohrium(action="submit")` 真正产生外部副作用前引入用户确认与参数审阅；用户可修改 submit 参数和 input_dir 内文件；tool_result 必须把用户修改后的参数和具体文件变化呈现给 agent。

## 1. 背景

当前 `BohriumTool` 的 submit 路径非常直接：

1. agent 生成 `Bohrium(action="submit", input_dir=..., image=..., cmd=...)`。
2. `FullToolRunner` 做通用工具链路校验。
3. `BohriumTool._submit()` 校验必填参数，填充默认 `machine`、`job_name`、`disk_size`。
4. `submit_job_via_runtime()` 解析 `input_dir`，打包或远端上传输入目录。
5. 调用 Bohrium `job/create` 与 `job/add`。
6. 返回只包含 `job_id`、提交状态和 sandbox 标记的 `ToolResult.content`。

这条路径缺少一个强制的人类确认点。Bohrium submit 是外部副作用：一旦 `job/create`、上传和 `job/add` 执行，平台资源就已经被使用，错误的 `image`、`cmd`、`machine` 或输入文件都可能造成浪费。

用户希望在 submit 前暂停，让前端展示待提交信息，并允许用户：

- 确认或拒绝本次 submit。
- 修改 `cmd`、`image`、`machine`、`job_name`、`disk_size`、`input_dir` 等 submit 参数。
- 修改 `input_dir` 内的具体输入文件。
- 让最终 tool_result 明确告知 agent：用户是否确认、哪些 submit 参数被改了、input_dir 内哪些具体文件被改了。

## 2. 目标

- 在 `Bohrium(action="submit")` 真正调用 `job/create`、上传输入或 `job/add` 前暂停，等待用户确认。
- 用户确认后，使用用户返回的最终 submit 参数继续执行 Bohrium submit。
- 用户拒绝后，不调用 `job/create`，不上传 input zip，不调用 `job/add`，并返回模型可见的 `ToolResult(status="blocked")`。
- 用户对 submit 参数的修改必须进入模型可见的 `ToolResult.content.review.parameter_changes`。
- 用户对 input_dir 内具体文件的修改必须进入模型可见的 `ToolResult.content.review.input_file_changes`。
- 文件内容不进入后端 review payload，不进入 tool_result；前端只把具体文件路径和可选摘要回传给后端。
- 完整审计记录进入 `ToolResult.payload.bohrium_submit_review`，供 SSE 前端和历史事件消费。
- 保持 API / Worker 分离。确认请求由 worker 发出，用户回复可以由任意 API 实例接收，通过 Redis reply queue 回到执行 run 的 worker。
- 不通过 `run_meta` 传递服务能力，不把用户交互 callback 塞进 metadata。

## 3. 非目标

- 不由后端读取、展示、diff 或写入 input_dir 内文件内容。
- 不在 hook payload 或 tool_result 中传输文件内容。
- 不新增主代码中的兼容兜底逻辑；旧事件或旧 tool_result 不做内联兼容。
- 不改变 Bohrium query、download、kill、list_images、list_machines 的语义。
- 不改变 Bohrium job ledger 的状态机和 DB schema。
- 不把通用 AskQuestion 工具当作 submit 确认的强制闸门；AskQuestion 由模型主动调用，不能保证贴着真实 submit 副作用执行。
- 不让前端通过 review reply 提交文件 patch。文件修改由前端通过文件系统能力提前完成，reply 只报告哪些文件被修改。

## 4. 现状代码事实

### 4.1 HookExecutor 边界

当前 hook 系统由 `HookExecutor` 承载，能力分为 observe、intercept、rewrite：

- `PRE_TOOL_CALL` 目前只做 observe 和 intercept，不能返回修改后的 arguments。
- `POST_TOOL_CALL` 当前支持 rewrite `ToolResult`。
- Hook 输入会被 deepcopy，observe 中的原地修改不会泄漏。

同时项目边界要求：`HookExecutor` 专指事件扩展系统，用于 observe、intercept、rewrite 运行过程事件。需要返回业务数据或承担顺序屏障语义的服务能力，不应伪装成 HookExecutor handler。

因此，本设计把用户确认本身建模为窄 runtime capability port，而不是塞进 `HookExecutor` handler。工具运行链路仍提供 submit review 这个生命周期点，并让现有 `POST_TOOL_CALL` observe/rewrite 能看到已经增强后的结果。

### 4.2 ToolRunner arguments 只读约束

`FullToolRunner.execute_batch()` 明确要求每层消费者把 arguments 当作只读。原因是 `ToolCallData` 缓存了 `arguments_json`，原地修改会造成缓存和 dict 不一致。

因此用户修改后的 submit 参数必须形成新的 `effective_args`，不能修改 `tc.arguments`。

### 4.3 ToolResult content 与 payload 的消费差异

`dispatch_tool_calls()` 会把 `ToolResult.content` 写入 `ToolMessage.content`。这是 agent 后续轮次可见的稳定内容。

`ToolResult.payload` 会进入 `ToolResultEvent.payload`，并在 SSE public content 中表现为 `tool_result.content.info`。这是前端展示和审计更适合使用的 carrier，但不应依赖它让 agent 理解用户改动。

因此：

- agent 必须知道的用户修改摘要放入 `ToolResult.content.review`。
- 前端和审计需要的完整结构放入 `ToolResult.payload.bohrium_submit_review`。

## 5. 总体决策

采用专用 Bohrium submit review gate：

1. 服务层在 `AgentRunPorts` 注入 `BohriumSubmitReviewPort`。
2. `Exp.build_runtime()` 将该 port 传给 `FullToolRunner`。
3. `FullToolRunner` 在 catalog lookup 后、structural validation 前识别 `Bohrium` submit 调用。
4. runner 生成规范化的 submit draft，调用 `BohriumSubmitReviewPort.review(...)`。
5. port 发出 `bohrium_submit_review` SSE 事件，并阻塞等待专用 reply API 写回 Redis reply queue。
6. 用户确认时，runner 使用最终 submit arguments 继续走 structural validation、input validation、capability policy、scheduler 和 tool executor。
7. 用户拒绝时，runner 直接返回 `ToolResult(status="blocked")`，不进入 Bohrium submit。
8. 工具执行结果或拒绝结果都会被 submit review result augmentation 增强，确保 `content.review` 与 `payload.bohrium_submit_review` 同时存在。
9. 通用 `POST_TOOL_CALL` observe/rewrite 在增强之后执行，外部 hook 看到的是已经携带用户审阅信息的 ToolResult。

```text
LLM tool call: Bohrium submit
  -> ToolRunner catalog lookup
  -> Bohrium submit review gate
       -> normalize original submit args
       -> emit bohrium_submit_review event
       -> wait Redis reply
       -> decision submit or reject
  -> reject:
       -> ToolResult(status="blocked", content.review, payload.bohrium_submit_review)
  -> submit:
       -> final args pass structural validation / input validation / policy
       -> scheduler
       -> BohriumTool._submit(final_args)
       -> augment ToolResult with content.review and payload.bohrium_submit_review
       -> generic POST_TOOL_CALL rewrite / observe
       -> ToolResultEvent / ToolMessage
```

## 6. RuntimePort 合同

### 6.1 新增 port

在 `AgentRunPorts` 增加窄能力端口：

```python
@runtime_checkable
class BohriumSubmitReviewPort(Protocol):
    async def review(
        self,
        request: BohriumSubmitReviewRequest,
    ) -> BohriumSubmitReviewDecision: ...
```

该 port 不包含 `extra`、`metadata`、`state`、`context`、`services`、`payload` 或 `dict[str, Any]` 兜底字段。请求和返回都使用明确字段的 dataclass 或 Pydantic model。

### 6.2 消费者

唯一消费者是 `FullToolRunner` 的 Bohrium submit review gate。

`BohriumTool` 不直接依赖 Redis、SSE、API service 或 reply queue；它仍然只负责 Bohrium 平台操作。

### 6.3 调用时机

调用条件必须同时满足：

- `tool_name == "Bohrium"`
- `arguments["action"] == "submit"`

调用位置在 structural validation 之前。这样用户修改后的 `input_dir`、`cmd`、`image`、`machine` 等最终参数仍会被后续 validation 和 policy 检查，不会绕过现有约束。

### 6.4 返回值语义

返回 `BohriumSubmitReviewDecision`，包含：

- `decision`: `submit`、`reject`、`timeout`、`cancelled`。
- `final_arguments`: 用户确认或拒绝时的最终 submit 草稿。
- `parameter_changes`: 原始参数到最终参数的字段级差异。
- `input_file_changes`: input_dir 内具体文件变化列表。
- `user_note`: 用户可选备注。
- `request_id`: review 请求 ID。

`parameter_changes` 可以由 runner 根据 original/final args 计算，也可以由 port 返回后由 runner 规范化；最终以 runner 计算结果为准，避免前端误报。

### 6.5 异常语义

- port 抛出异常：不提交 Bohrium 作业，返回 `ToolResult(status="blocked")` 或 `ToolResult(status="error")`。第一版推荐 `blocked`，content 明确表示 submit review 不可用，外部副作用未发生。
- 用户拒绝：返回 `blocked`，不是 `error`。
- 用户超时：返回 `blocked`，message 表达等待用户确认超时，作业未提交。
- run 被 stop：返回 `cancelled` 或让上层取消路径处理，作业未提交。
- port 缺失：生产路径视为 review 不可用并阻断 submit；测试或 devshell 如果需要无确认 submit，应显式配置一个 no-review 或 auto-approve port，而不是主代码里隐式兜底。

## 7. 数据模型

### 7.1 Submit arguments

第一版 submit review 关注这些字段：

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

runner 在发起 review 前必须先做 submit args 规范化：

- 必填：`input_dir`、`image`、`cmd`。
- 默认：`machine="c32_m128_cpu"`、`job_name="matmaster-job"`、`disk_size=50`。
- `disk_size` 规范化为 int。
- `cmd` 是否追加 `> log 2>&1` 需要和 `_submit()` 现有行为保持一致。为避免用户确认界面看到的命令和真实提交命令不一致，推荐把该逻辑抽到共享 `normalize_submit_args()` 中。

### 7.2 ParameterChange

用户对 submit 参数的修改记录：

```json
{
  "field": "cmd",
  "from_value": "python run.py > log 2>&1",
  "to_value": "python run.py --ecut 600 > log 2>&1"
}
```

`parameter_changes` 在 tool_result 中可以投影成 map，便于 agent 读取：

```json
{
  "cmd": {
    "from": "python run.py > log 2>&1",
    "to": "python run.py --ecut 600 > log 2>&1"
  },
  "image": {
    "from": "old-image",
    "to": "new-image"
  }
}
```

参与 diff 的字段为：

- `input_dir`
- `image`
- `cmd`
- `machine`
- `job_name`
- `disk_size`

### 7.3 InputFileChange

前端负责展示、读取和修改文件。后端只接收具体文件变化事实：

```json
{
  "path": "/share/case_001/input.inp",
  "relative_path": "input.inp",
  "operation": "modified",
  "summary": "用户修改了 cutoff 和收敛阈值"
}
```

字段语义：

- `path`: 文件完整路径。
- `relative_path`: 相对最终 `input_dir` 的路径。
- `operation`: `created`、`modified`、`deleted`、`renamed`。
- `summary`: 可选，用户或前端提供的短摘要。
- `previous_path`: 可选，仅 `renamed` 使用，表示重命名前路径。

后端不读取文件内容，也不验证文件内容是否真的改变。后端只做路径边界校验和结构校验。

### 7.4 Review request SSE

Worker 发出 `bohrium_submit_review` 事件：

```json
{
  "type": "bohrium_submit_review",
  "source": "System",
  "request_id": "bsr_xxx",
  "tool_call_id": "call_xxx",
  "submit_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "old-image",
    "cmd": "python run.py > log 2>&1",
    "machine": "c32_m128_cpu",
    "job_name": "case-001",
    "disk_size": 50
  },
  "input_dir": "/share/case_001"
}
```

事件不包含文件内容。前端拿到 `input_dir` 后，通过文件浏览和编辑能力展示具体文件。

### 7.5 Review reply

用户确认提交：

```json
{
  "request_id": "bsr_xxx",
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
  "input_file_changes": [
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
  "user_note": "已确认提交"
}
```

用户拒绝提交：

```json
{
  "request_id": "bsr_xxx",
  "decision": "reject",
  "submit_arguments": {
    "action": "submit",
    "input_dir": "/share/case_001",
    "image": "new-image",
    "cmd": "python run.py --dry-run > log 2>&1",
    "machine": "c32_m128_cpu",
    "job_name": "case-001",
    "disk_size": 50
  },
  "input_file_changes": [
    {
      "path": "/share/case_001/input.inp",
      "relative_path": "input.inp",
      "operation": "modified"
    }
  ],
  "user_note": "先暂停，我还要继续改参数"
}
```

拒绝时的 `submit_arguments` 是用户当前草稿，不会被提交，但仍用于生成 `parameter_changes`，让 agent 知道用户已经改过哪些参数。

## 8. ToolResult 合同

### 8.1 成功提交

用户确认后，Bohrium submit 成功返回：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true,
  "review": {
    "user_confirmed": true,
    "message": "用户确认了 Bohrium 提交，并修改了提交参数和输入文件。",
    "changed_fields": ["image", "cmd", "machine", "job_name", "disk_size"],
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
      },
      "job_name": {
        "from": "case-001",
        "to": "case-001-reviewed"
      },
      "disk_size": {
        "from": 50,
        "to": 80
      }
    },
    "input_file_changes": [
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
    ]
  }
}
```

这段 JSON 是 `ToolResult.content`，进入 `ToolMessage.content`，agent 后续可以直接读到。

### 8.2 用户拒绝

用户拒绝时：

```json
{
  "success": false,
  "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。请暂停，不要再次提交，等待用户补充反馈或修改参数后再继续。",
  "review": {
    "user_confirmed": false,
    "changed_fields": ["cmd", "image"],
    "parameter_changes": {
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --dry-run > log 2>&1"
      },
      "image": {
        "from": "old-image",
        "to": "new-image"
      }
    },
    "input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified",
        "summary": "用户还在调整参数，暂不提交"
      }
    ],
    "user_note": "参数还没最终确认，先不要提交。"
  }
}
```

对应 `ToolResult.status` 为 `blocked`。

拒绝不是平台错误，也不是异常。它表示外部副作用被用户拦截，agent 必须暂停，不能自动换参数重试 submit。

### 8.3 ToolResult.payload 审计结构

无论用户确认还是拒绝，`ToolResult.payload.bohrium_submit_review` 都包含完整审计：

```json
{
  "bohrium_submit_review": {
    "request_id": "bsr_xxx",
    "tool_call_id": "call_xxx",
    "decision": "submit",
    "submitted": true,
    "original_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "old-image",
      "cmd": "python run.py > log 2>&1",
      "machine": "c32_m128_cpu",
      "job_name": "case-001",
      "disk_size": 50
    },
    "final_arguments": {
      "action": "submit",
      "input_dir": "/share/case_001",
      "image": "new-image",
      "cmd": "python run.py --ecut 600 > log 2>&1",
      "machine": "c64_m256_cpu",
      "job_name": "case-001-reviewed",
      "disk_size": 80
    },
    "changed_fields": ["image", "cmd", "machine", "job_name", "disk_size"],
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
    "input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified",
        "summary": "用户修改了 cutoff 和收敛阈值"
      }
    ],
    "user_note": "已确认提交"
  }
}
```

`payload` 可以比 `content.review` 更完整，但不包含文件内容。

## 9. 路径与文件变化校验

后端不读取文件内容，但必须做路径边界校验：

1. 最终 `submit_arguments.input_dir` 是路径基准。
2. `input_file_changes[*].path` 必须位于最终 input_dir 下。
3. 如果前端只传 `relative_path`，后端用最终 input_dir 拼出 path。
4. 如果前端同时传 path 和 relative_path，后端校验二者一致。
5. `relative_path` 不允许为空，不允许以 `/` 开头，不允许包含 `..` 逃逸。
6. `operation` 只允许 `created`、`modified`、`deleted`、`renamed`。
7. `renamed` 可带 `previous_path` 或 `previous_relative_path`，同样受 input_dir 边界约束。
8. 如果文件变化数量过多，`ToolResult.content.review.input_file_changes` 可以保留前 N 个和总数；`ToolResult.payload.bohrium_submit_review.input_file_changes` 保存完整列表。

第一版建议 content 中最多展示 20 个文件变化，并增加：

```json
{
  "input_file_change_count": 37,
  "input_file_changes_truncated": true
}
```

这样 agent 能知道还有更多文件被改过，前端仍可以从 payload 看到完整列表。

## 10. API / Worker 通信

### 10.1 Review bridge

新增 `BohriumSubmitReviewBridge`，结构上接近 `AskQuestionBridge`：

- 持有 `session_id`。
- 通过 event sink 派发 `BohriumSubmitReviewEvent`。
- 通过 Redis reply queue 阻塞等待 reply。
- 用 `request_id` 校验回复匹配。
- 用 `asyncio.Lock` 保证同一 session 内一次只等待一个 review reply。

### 10.2 Reply API

新增 endpoint：

```text
POST /api/v1/chat/sessions/{session_id}/bohrium_submit_review_reply
```

请求体为 `ChatBohriumSubmitReviewReplyRequest`，包含：

- `request_id`
- `decision`
- `submit_arguments`
- `input_file_changes`
- `user_note`

API 行为：

1. 校验用户有 session 权限。
2. 校验当前 session 有 active run。
3. 发布 `bohrium_submit_review_reply` 事件到 stream。
4. 将结构化 reply envelope 写入 Redis reply queue。
5. 写入历史事件表。

### 10.3 Queue 复用

可以复用现有 interaction reply Redis list，但 envelope 必须带明确 kind：

```json
{
  "kind": "bohrium_submit_review_reply",
  "body": {
    "request_id": "bsr_xxx",
    "decision": "submit",
    "submit_arguments": {},
    "input_file_changes": []
  }
}
```

`AskQuestionBridge` 和 `BohriumSubmitReviewBridge` 都必须校验 kind 和 request_id。错误 kind 或错误 request_id 不能被误消费为有效回复。

## 11. ToolRunner 集成

### 11.1 新增 review gate

`FullToolRunner.execute_batch()` 当前串行 validation 阶段需要插入 Bohrium submit review：

```text
catalog lookup
raw JSON rejection
base_args deepcopy
Bohrium submit review gate
  -> reject: result blocked, skip validation/execution
  -> submit: effective_args = final_arguments
generic PRE_TOOL_CALL observe/intercept
structural validation
input_validator
capability policy
fast path / approved
```

需要注意当前代码先执行 generic PRE_TOOL_CALL observe/intercept，再 structural validation。为了让用户最终参数也能被 generic pre hooks 看到，本设计建议顺序为：

```text
submit review gate
generic PRE_TOOL_CALL observe/intercept using reviewed args
structural validation using reviewed args
```

这样通用 hook 不会观察到已经被用户替换掉的旧参数。

### 11.2 审计记录携带

runner 在 review gate 得到 `BohriumSubmitReviewRecord` 后，按 `tool_call_id` 持有该记录，直到：

- 用户拒绝时直接组装 blocked ToolResult。
- 用户确认且工具执行后增强 ToolResult。
- validation 或 policy 拒绝最终参数时，也增强错误 ToolResult，让 agent 知道是用户修改后的最终参数没有通过校验。

### 11.3 ToolCallEvent arguments 不回写

`ToolCallEvent.arguments` 仍表示模型原始 tool call 参数，不回写用户修改后的 final args。真实提交参数由 `tool_result.content.review` 和 `tool_result.payload.bohrium_submit_review.final_arguments` 表达。

这是有意设计：tool_call 表示模型意图，tool_result 表示实际执行结果和人类介入记录。

## 12. BohriumTool 调整

`BohriumTool._submit()` 当前内部填默认参数。为了让 review UI 看到的草稿与真实提交一致，需要抽出共享规范化函数：

```python
def normalize_bohrium_submit_arguments(args: BohriumSubmitArguments) -> BohriumSubmitArguments:
    ...
```

该函数承担：

- 必填字段校验前的基础字符串规整。
- 默认 `machine`、`job_name`、`disk_size`。
- `disk_size` int 转换。
- `cmd` 末尾 `> log 2>&1` 的统一处理。

`_submit()` 和 review gate 共用它，避免确认界面看到一个 cmd，而真实 submit 又自动追加另一个 cmd。

## 13. 事件投影

新增 public content mapping：

### 13.1 bohrium_submit_review

SSE content 包含：

- `request_id`
- `tool_call_id`
- `submit_arguments`
- `input_dir`

不包含文件内容。

### 13.2 bohrium_submit_review_reply

SSE content 包含：

- `request_id`
- `decision`
- `submit_arguments`
- `input_file_changes`
- `user_note`

如果文件变化很多，前端 reply event 可以完整展示，因为这是用户交互事件；tool_result content 再做 N 条限制。

### 13.3 tool_result

无需改变通用 tool_result 投影规则。现有 `ToolResult.payload` 会进入 `info`，因此 `bohrium_submit_review` 审计结构自然出现在前端。

## 14. 错误与边界

### 14.1 用户拒绝

返回 `ToolResult(status="blocked")`，content 必须包含：

- `success=false`
- `status="UserRejected"`
- 暂停等待用户反馈的明确 message
- 参数变化
- input_dir 内具体文件变化
- user_note

不提交作业。

### 14.2 用户超时

返回 `ToolResult(status="blocked")`，content 表示：

- 等待用户确认超时。
- Bohrium 作业没有提交。
- agent 应暂停等待用户继续反馈，不要自动重试 submit。

### 14.3 Stop / cancel

如果用户 stop 当前 run：

- reply queue 会收到 cancel sentinel 或 cancel_token 会触发。
- review bridge 结束等待。
- 不提交作业。
- 工具结果走 cancelled 或上层 run cancelled 语义。

### 14.4 Review port 缺失

生产 worker 队列模式必须注入 review port。缺失时不允许静默提交。

如果 devshell 或测试场景需要跳过确认，必须显式注入 auto-approve port。这样行为是显式测试配置，不是主代码兼容分支。

### 14.5 用户最终参数无效

用户确认后的参数仍然走 structural validation、input validation 和 policy。

如果最终参数无效，返回对应 validation/policy ToolResult，并增强 review 信息，让 agent 知道：

- 用户确认过。
- 用户改了哪些参数和文件。
- 最终参数为什么没有通过校验。

## 15. 测试计划

Focused tests：

1. `FullToolRunner` 在 Bohrium submit 前调用 review port，且调用发生在 structural validation 前。
2. 用户修改 `image`、`cmd`、`machine`、`job_name`、`disk_size` 后，最终 `_submit()` 收到 final arguments。
3. 用户修改 `input_dir` 后，最终 structural validation 和 `_submit()` 使用新的 input_dir。
4. 用户返回 `input_file_changes` 后，`ToolResult.content.review.input_file_changes` 包含具体文件路径和 relative_path。
5. 用户拒绝时返回 `ToolResult(status="blocked")`，且不调用 `create_job`、`upload_input_archive`、`add_job`。
6. 成功 submit 后，`ToolResult.content.review.parameter_changes` 和 `ToolResult.payload.bohrium_submit_review.parameter_changes` 都存在。
7. `ToolResult.payload.bohrium_submit_review` 进入 SSE `tool_result.content.info`。
8. 文件内容不会出现在 review request、review reply、tool_result content 或 payload。
9. reply kind 或 request_id 不匹配时，bridge 拒绝消费。
10. 多个 Bohrium submit review 在同一 session 内被 lock 串行处理。
11. stop 当前 run 时，正在等待的 submit review 被唤醒并且不提交作业。
12. 用户确认后的 final args 如果被 structural validation 拒绝，返回结果仍附带 review record。

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

## 16. 实施顺序建议

1. 定义 review 数据结构和 port 协议。
2. 新增 `BohriumSubmitReviewBridge` 和 reply API。
3. 将 `AgentRunService` 中现有 interaction event sink / Redis reply queue 复用于 review bridge。
4. `AgentRunPorts` 注入 `bohrium_submit_reviewer`。
5. `Exp.build_runtime()` 将 port 交给 `FullToolRunner`。
6. `FullToolRunner` 新增 Bohrium submit review gate，并保持 final args 走后续 validation/policy。
7. 抽出 Bohrium submit args 规范化函数。
8. 增强 ToolResult content 和 payload。
9. 增加 event payload mapping 和 focused tests。

## 17. 最终行为示例

### 17.1 用户确认并修改参数和文件

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

用户在前端：

- 修改 `image` 为 `new-image`。
- 修改 `cmd` 为 `python run.py --ecut 600 > log 2>&1`。
- 修改 `machine` 为 `c64_m256_cpu`。
- 修改 `/share/case_001/input.inp`。
- 修改 `/share/case_001/run.sh`。
- 点击确认提交。

最终 agent 看到的 tool_result：

```json
{
  "success": true,
  "job_id": "12345",
  "status": "Submitted",
  "use_sandbox": true,
  "review": {
    "user_confirmed": true,
    "message": "用户确认了 Bohrium 提交，并修改了提交参数和输入文件。",
    "changed_fields": ["image", "cmd", "machine"],
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
    "input_file_changes": [
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
    ]
  }
}
```

### 17.2 用户拒绝提交但保留草稿修改

用户在前端：

- 修改 `cmd`。
- 修改 `input.inp`。
- 点击拒绝提交，并备注参数还没最终确认。

agent 看到：

```json
{
  "success": false,
  "status": "UserRejected",
  "message": "用户拒绝了本次 Bohrium 提交。请暂停，不要再次提交，等待用户补充反馈或修改参数后再继续。",
  "review": {
    "user_confirmed": false,
    "changed_fields": ["cmd"],
    "parameter_changes": {
      "cmd": {
        "from": "python run.py > log 2>&1",
        "to": "python run.py --dry-run > log 2>&1"
      }
    },
    "input_file_changes": [
      {
        "path": "/share/case_001/input.inp",
        "relative_path": "input.inp",
        "operation": "modified"
      }
    ],
    "user_note": "参数还没最终确认，先不要提交。"
  }
}
```

agent 后续应停止自动 submit，等待用户继续反馈。
