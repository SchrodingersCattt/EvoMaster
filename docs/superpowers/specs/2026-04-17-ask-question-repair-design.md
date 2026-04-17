# AskQuestion 完整闭环修复设计

- 日期: 2026-04-17
- 状态: Draft v0.2 (待评审)
- 相关仓库:
  - 后端: `matmaster-evo`
  - 前端: `../scimaster-bohr-chat`
- 相关模式: `direct`, `planner`

## 1. 背景

当前 checkout 中，AskQuestion 处于半恢复状态。代码库里已经重新出现了
`AskQuestionTool`、`AskQuestionBridge`、`ask_question` 事件族，以及前端
`AskQuestionWizard`，但端到端链路没有打通。

已确认的主要断点有四个：

1. 后端 `matmaster/exps/direct.toml` 与 `matmaster/exps/planner.toml` 声明了
   `AskQuestion`，但 `matmaster/core/exp.py` 的 `_init_builtin_tools()` 没有实例化
   或注册 `AskQuestionTool`。因此模型拿不到 `AskQuestion` tool definition，正常运行中
   无法触发它。
2. 前端 `postEvoAskQuestionReply()` 调用
   `POST /chat/sessions/{session_id}/ask_question_reply`，但后端当前只实现了
   `POST /chat/sessions/{session_id}/confirmation_reply`。即使手动让后端发出
   `ask_question` SSE，用户提交答案也会失败。
3. `AskQuestionBridge` 当前直接调用 `send_cb` 推 live SSE，没有走
   `RunEventFanout` 的 `SSEHandler + PersistenceHandler` 统一路径。因此 live 连接能收到
   `ask_question`，但历史持久化和刷新恢复语义不完整。
4. `AgentRunService` 当前通过 `pg_ctx.model_copy(update={"interaction_bridge": bridge})`
   注入 bridge，但 `PlaygroundContext` 没有声明 `interaction_bridge` 字段。这依赖
   Pydantic `model_copy(update=...)` 的隐式动态属性行为，`model_dump()` 中也不会出现该字段，
   不应继续作为运行时合同。

另外还发现一个现有 bug：`ChatStreamService.broadcast_reply()` 的默认
`event_type` 是 `"ask_question_reply"`，而当前 `confirmation_reply` endpoint 调用时没有显式
传 `event_type`。因此 live SSE 里 confirmation 回复可能被错误广播为
`ask_question_reply`。这次重构 interaction reply helper 时必须顺手修掉，并加回归测试。

这次修复目标是完整闭环，而不是只让当前 live 页面临时可见。

## 2. 目标

- 模型能在 `direct` 和 `planner` 模式下看到并调用 `AskQuestion`
- 调用 `AskQuestion` 后，后端发出 `ask_question` SSE，前端显示结构化问答卡片
- 用户提交结构化答案后，后端唤醒阻塞中的 AskQuestion tool，agent 继续执行
- `ask_question`、`ask_question_reply`、`ask_question_timeout` 进入历史，刷新页面后能恢复等待中的问答状态
- 复用现有 `confirmation_reply` 的底层运行基础设施，包括 Redis reply list、active run 判定、run context、stop 取消唤醒、SSE 广播和历史写入模式
- 保持 `confirmation_reply` 作为纯文本确认回复协议，避免把结构化 AskQuestion 答案塞进字符串
- 将 AskQuestion bridge 从隐式动态属性提升为 `PlaygroundContext` 的显式字段合同
- 修复 `broadcast_reply()` 默认事件类型导致 confirmation live SSE type 错误的问题

## 3. 非目标

- 不重做前端 AskQuestion UI。现有 `AskQuestionWizard`、`InteractionCard`、`activeInteraction`
  store 继续沿用
- 不把 planner_ask / ask_human 全部迁移到 AskQuestion
- 不新增数据库表
- 不强制迁移现有 Redis key 名称。当前
  `chat:confirmation_reply:{session_id}` 可以继续作为 interaction reply list 使用
- 不在第一版加入独立 pending request Redis key 来校验 `request_id`
- 不修改历史中已有 `confirmation_reply` 事件的兼容行为
- 不改变前端 `confirmation_reply` API 形状，仍然使用 `{ content: string }`

## 4. 现有 confirmation_reply 链路

后端已有的确认回复链路不是废弃代码，应作为 AskQuestion 的底层基础。

当前链路如下：

```text
worker 开始 run
  -> delete_interaction_reply_list(session_id)
  -> set_interaction_run_active(session_id)
  -> set_interaction_run_context(session_id, task_id, invocation_id)

前端提交 confirmation_reply
  -> POST /chat/sessions/{session_id}/confirmation_reply
  -> can_access_session()
  -> stream_svc.get_reply_queue(session_id)
  -> stream_svc.broadcast_reply(content=text)
  -> reply_queue.put_content(text)
  -> events_svc.add_history_event(type="confirmation_reply", content=text)

worker/bridge 等待回复
  -> RedisReplyQueue.get()
  -> redis BLPOP reply list

stop session
  -> reply_queue.put_cancel()
  -> 阻塞等待被唤醒并取消
```

注意：上面是当前代码的调用链，不是完全正确的语义。由于
`broadcast_reply()` 的默认 `event_type` 当前是 `"ask_question_reply"`，而
`confirmation_reply` endpoint 没有显式传参，live SSE 可能广播出错误类型。修复后
`confirmation_reply` 必须显式传 `event_type="confirmation_reply"`，并用回归测试固定。

可复用部分：

- `RedisReplyQueue`
- `get_reply_queue()`
- `get_run_context()`
- `broadcast_reply()`
- worker 对 active run、run context、reply list 的生命周期管理
- stop 时写入取消哨兵唤醒阻塞线程
- API 权限校验和 409 行为模式
- 历史写入时补 `task_id` / `invocation_id`

不可直接复用部分：

- `ChatPlannerReplyRequest(content: str)` 请求体
- `confirmation_reply` 事件类型
- queue 中纯文本 reply 的语义

AskQuestion 的回复天然是结构化对象：

```json
{
  "request_id": "aq_xxx",
  "answers": {
    "Which architecture?": "Layered"
  },
  "annotations": {
    "Which architecture?": {
      "freeform": "Keep API stable"
    }
  }
}
```

因此本设计选择复用 confirmation 的底层 transport，而不是复用 confirmation 的 HTTP/API 协议。

## 5. 方案选择

### 方案 A: 独立 ask_question_reply 协议，共用底层 interaction transport

新增 `POST /ask_question_reply`，body 使用结构化 schema；底层仍使用现有 Redis reply
list、active run、run context、broadcast/history 流程。

优点：

- 协议清晰，`confirmation_reply` 是文本确认，`ask_question_reply` 是结构化问答
- 与前端现有 `postEvoAskQuestionReply()` 路径一致
- 保留结构化答案，便于历史恢复、导出和后续分析
- 最大化复用现有 worker/Redis 生命周期

缺点：

- 需要新增 request model、endpoint 和测试
- 需要调整 bridge 事件发送路径，避免绕过 persistence

### 方案 B: 新增统一 interaction_reply 协议

新增 `POST /interaction_reply`，body 带 `kind` 和 `payload`，让 confirmation 和 AskQuestion
都走新接口。

优点：

- 从长期架构看最统一，未来可扩展更多中途交互类型

缺点：

- 前后端迁移面更大
- 老 `/confirmation_reply` 仍需保留兼容，短期接口更多
- 当前需求只修 AskQuestion，性价比不高

### 方案 C: AskQuestion 复用 confirmation_reply

前端把 AskQuestion payload stringify 后发到 `/confirmation_reply`。

优点：

- 路由改动最少

缺点：

- 结构化语义退化成字符串
- 历史恢复和导出需要二次解析
- HTTP 是 confirmation，SSE 又是 ask_question_reply，协议不一致
- 后续维护成本高

本设计采用方案 A。

## 6. 后端设计

### 6.0 显式声明 PlaygroundContext.interaction_bridge

第一步先修正 bridge 的传递合同。当前 `PlaygroundContext` 是 frozen Pydantic model：

```python
model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
```

它没有 `extra="allow"`，也没有声明 `interaction_bridge` 字段。当前
`pg_ctx.model_copy(update={"interaction_bridge": bridge})` 在本地 Pydantic 版本下会让
`getattr(ctx, "interaction_bridge", None)` 取到值，但该字段不会出现在 `model_dump()` 中，
这属于隐式动态属性，不适合作为 AskQuestion 能否暴露给模型的根基。

本设计选择显式字段方案，而不是把 bridge 放进 `run_meta`：

```python
interaction_bridge: Any = Field(default=None, repr=False)
```

选择理由：

- bridge 是运行时对象，不是可序列化 metadata，不适合放入 `run_meta`
- 显式字段可被 IDE、类型检查、测试和代码搜索发现
- `repr=False` 避免日志或调试输出里打印包含闭包的 bridge 对象
- `Exp._init_builtin_tools()` 可以直接读取 `ctx.interaction_bridge`，不再依赖 `getattr`

`AgentRunService` 仍用 `pg_ctx.model_copy(update={"interaction_bridge": bridge})` 注入，但该
update 现在针对声明字段，语义稳定。

测试要求：

- `PlaygroundContext(..., interaction_bridge=bridge)` 能构造
- `AgentRunService` 注入 bridge 后，传入 `Exp.build_runtime()` 的 ctx 上
  `ctx.interaction_bridge` 非空
- 无 bridge 时默认值为 `None`

### 6.1 AskQuestionTool 注册

`AskQuestionTool` 已有关键保护逻辑：构造时如果没有 bridge，会设置
`self.exposed_to_model = False`。因此可以安全注册，但只在 bridge 存在时暴露给模型。

变更点：

- `matmaster/tools/builtin/__init__.py` 导出 `AskQuestionTool`
- `matmaster/core/exp.py::_init_builtin_tools()` 实例化并按 builtin config 过滤注册
- `AskQuestionTool` 归类为 control-plane / interaction tool，而不是 session filesystem tool

建议构造方式：

```python
AskQuestionTool(
    session=ctx.session,
    workdir=Path(ctx.execution_workdir) if ctx.session is not None else ctx.workdir,
    bridge=ctx.interaction_bridge,
)
```

行为规则：

- `builtin=["AskQuestion"]` 时应注册 AskQuestion
- `builtin=["*"]` 时应注册 AskQuestion
- bridge 存在时 model-visible
- bridge 不存在时工具可在 catalog 中存在，但不出现在 model tool definitions 中

### 6.2 ask_question_reply request model

新增模型：

```python
class ChatAskQuestionReplyRequest(BaseModel):
    request_id: str
    answers: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, dict[str, str]] = Field(default_factory=dict)
```

基础校验：

- `request_id.strip()` 必须非空
- `answers` 和 `annotations` 不应同时为空

如果 Pydantic model validator 会引入过多样板，第一版可以在 endpoint 中做显式校验并返回
400/422；但错误类型应在测试里固定，避免前端难以判断。

### 6.3 通用 interaction reply helper

将 `confirmation_reply` endpoint 中的重复流程收敛成 helper，避免复制一份几乎相同的
active run、broadcast、queue、history 逻辑。

这一步同时修复现有 `broadcast_reply()` 默认事件类型 bug：

- `confirmation_reply` endpoint 必须显式传 `event_type="confirmation_reply"`
- 新增 `ask_question_reply` endpoint 必须显式传 `event_type="ask_question_reply"`
- `broadcast_reply()` 的默认值应改回更安全的 `"confirmation_reply"`，或者去掉默认值并强制调用方传入

推荐去掉默认值，因为两种 reply 事件都很重要，隐式默认容易再次引入错发类型。

建议 helper 放在 `src/apis/chat_api.py` 本文件内，先保持局部私有，避免过早抽象到 service：

```python
def _submit_interaction_reply(
    *,
    sid: str,
    event_type: str,
    content: str | dict,
    queue_value: str,
    stream_svc: ChatStreamService,
    events_svc: ChatEventsService,
    user_id: str | None,
) -> None:
    ...
```

职责：

- 通过 `stream_svc.get_reply_queue(sid)` 获取当前 run 的 reply queue
- 无 active run 时抛 409，与现有 confirmation 行为一致
- 先 `stream_svc.broadcast_reply(sid, content, event_type=event_type)`
- 再 `reply_queue.put_content(queue_value)`
- 构造 history payload，补 `task_id` / `invocation_id`
- `events_svc.add_history_event(...)`

`confirmation_reply` 使用：

```python
content = (req.content or "").strip()
_submit_interaction_reply(
    event_type="confirmation_reply",
    content=content,
    queue_value=content,
    ...
)
```

`ask_question_reply` 使用：

```python
content = {
    "request_id": req.request_id.strip(),
    "answers": req.answers,
    "annotations": req.annotations,
}
queue_value = json.dumps({"payload": content}, ensure_ascii=False)
_submit_interaction_reply(
    event_type="ask_question_reply",
    content=content,
    queue_value=queue_value,
    ...
)
```

这样 `AskQuestionBridge._wait_for_reply()` 现有的 JSON envelope 解析逻辑可以直接复用。

### 6.4 broadcast_reply 支持结构化 content

`ChatStreamService.broadcast_reply()` 当前类型标注是 `content: str`，实际 payload 构造可以接受
dict。需要把类型放宽为 `str | dict` 或 `object`，并保持公开 SSE payload 为：

```json
{
  "source": "User",
  "type": "ask_question_reply",
  "content": {
    "request_id": "aq_xxx",
    "answers": {},
    "annotations": {}
  },
  "session_id": "sess",
  "task_id": "task",
  "invocation_id": "inv"
}
```

confirmation 的 live payload 不变：

```json
{
  "source": "User",
  "type": "confirmation_reply",
  "content": "确认，继续执行",
  "session_id": "sess",
  "task_id": "task",
  "invocation_id": "inv"
}
```

放宽 `content` 类型前需要审计下游消费点：

- 本地 SSE queue：`StreamQueueManager.broadcast()` 只传 dict payload，不应假设 content 是字符串
- 跨 worker Redis pubsub：`RedisDao.publish_stream_event()` 使用 `json.dumps(payload, ensure_ascii=False)`，dict content 可序列化即可
- SSE formatter：`ChatStreamService.sse_format()` 需要只序列化整个 payload，不应对 content 做字符串拼接
- 前端 `ask_question_reply` handler 已按 object content 解析；`confirmation_reply` handler 仍按 string content 解析
- History persistence：API 手动写 `ask_question_reply` 时 content 是 dict，`ChatEventsService.add_history_event()` 与 table 层应保持 JSON content 能往返

### 6.5 AskQuestion request 事件走 fanout

当前 `AskQuestionBridge` 直接调用 `send_cb`，绕过 persistence。应改为事件级 emit，生产路径走
`RunEventFanout`。

推荐设计：

- `AskQuestionBridge` 接收 `event_sink: Callable[[BusEvent], Awaitable[None]]`
- `ask()` 中构造 `AskQuestionEvent` 后调用 event sink
- timeout 时构造 `AskQuestionTimeoutEvent` 后也调用 event sink
- `AgentRunService` 注入 sink 时使用当前 run 的 `fanout.dispatch`

这是一次明确的 bridge API breaking change：

- 从 sync `send_cb(dict)` 改为 async `event_sink(BusEvent)`
- 从传 public-ish `model_dump` dict 改为传强类型 `AskQuestionEvent` / `AskQuestionTimeoutEvent`
- 不保留 dict payload 兼容 shim，避免同一 bridge 同时支持两种事件协议
- 测试 fake 统一改为 `async def fake_sink(event): events.append(event)`

由于 AskQuestion tool 在 agent async flow 中运行，`ask()` 本身已经是 async，因此 request
事件可以直接 `await event_sink(event)`。`_wait_for_reply()` 在 `asyncio.to_thread()` 内运行，
timeout 分支如果需要发事件，不能直接 await。更稳妥的设计是避免在线程函数里发事件：

```text
ask()
  -> await emit AskQuestionEvent
  -> try await to_thread(wait_for_reply)
  -> except TimeoutError:
       await emit AskQuestionTimeoutEvent
       raise
```

这样所有事件 emit 都留在 event loop 线程里，和 `RunEventFanout` 的 async API 对齐。

同步等待函数仍负责把 `queue.Empty` 转成 `TimeoutError`，确保 `asyncio.to_thread()` 在 await
点抛出的异常类型稳定：

```python
def _wait_for_reply_sync(self, request_id: str) -> dict[str, Any]:
    try:
        raw = self._reply_queue.get(timeout=self._timeout_seconds)
    except queue.Empty:
        raise TimeoutError(...) from None
    ...
```

`asyncio.CancelledError` 仍用于取消哨兵，但取消事件不在 bridge 内新增自定义
`ask_question_cancelled`。第一版复用现有 run terminal events 清理前端 pending 状态。

为未来并发问题留观测点：

- `AskQuestionBridge` 可维护进程内 `_waiting_request_id: str | None`
- 进入 `ask()` 时如果已有等待中的 request，写 warning 日志
- 正常 reply、timeout、cancel 后清空该字段
- 第一版不把该状态写入 Redis，也不做跨 worker pending request 校验

### 6.6 persistence 与历史恢复

`PersistenceHandler` 已经会持久化非 streaming 事件，并通过 `_public_content_for_event()`
把 `ask_question`、`ask_question_reply`、`ask_question_timeout` 写成前端可消费 content。

AskQuestion request 事件走 fanout 后，历史中会出现：

```text
ask_question
ask_question_reply
tool_result AskQuestion
...
```

或 timeout 场景：

```text
ask_question
ask_question_timeout
error/run_result/stream_closed
```

或用户 stop/cancel 场景：

```text
ask_question
cancelled / run_result(cancelled) / stream_closed(cancelled)
```

前端已有 `pendingAskQuestionFromHistoryRef`：

- 历史加载看到 `ask_question`，暂存等待中的 interaction
- 后续看到同 request_id 的 `ask_question_reply` 或 `ask_question_timeout`，清掉 pending
- 后续看到同一 run 的 terminal failure/cancel 事件，也应清掉 pending
- 历史加载结束仍有 pending，则恢复 AskQuestion 卡片

因此只要后端正确持久化事件，刷新恢复逻辑可以复用。

## 7. 前端设计

前端主体逻辑保持不变。

现有可复用部分：

- `buildAskQuestionInteraction()`
- `AskQuestionWizard`
- `InteractionCard`
- `activeInteraction`
- `postEvoAskQuestionReply()`
- `ask_question_reply` SSE handler
- 历史加载 pending 恢复逻辑

需要重点验证：

- `/ask_question_reply` 路径与后端新增 endpoint 一致
- 提交成功后的 optimistic user message 不与 live SSE 中的 `ask_question_reply` 重复
- `ask_question_reply.content` 是对象时 handler 正常解析
- 历史中 `ask_question` 后无 reply 且无 terminal cancel/failure 时恢复卡片
- 历史中有 reply/timeout/cancel/error/terminal failure 时不恢复卡片

若后端修复后发现 optimistic insert 与 SSE insert 重复，优先调整前端去重逻辑，不改变后端事件语义。

## 8. 数据流

### 8.1 正常问答

```text
LLM tool call: AskQuestion
  -> AgentKernel emits ToolCallEvent(AskQuestion)
  -> FullToolRunner executes AskQuestionTool
  -> AskQuestionBridge emits AskQuestionEvent via fanout
  -> SSEHandler sends ask_question
  -> PersistenceHandler stores ask_question
  -> frontend renders AskQuestionWizard
  -> user submits /ask_question_reply
  -> API validates active run
  -> API broadcasts ask_question_reply
  -> API pushes JSON envelope into RedisReplyQueue
  -> API stores ask_question_reply
  -> AskQuestionBridge receives envelope
  -> AskQuestionTool returns ToolResult(status=success, payload={request_id, questions, answers, annotations})
  -> AgentKernel emits ToolResultEvent(AskQuestion)
  -> model continues with user's structured answer
```

### 8.2 用户停止

```text
user clicks stop
  -> POST /stop
  -> stream_svc.get_reply_queue()
  -> reply_queue.put_cancel()
  -> AskQuestionBridge receives cancel sentinel
  -> raises CancelledError
  -> run cancellation path emits terminal cancellation/failure events and closes stream
  -> frontend history replay uses terminal events to clear pending AskQuestion
```

该路径复用现有 confirmation stop 行为。

### 8.3 超时

```text
AskQuestionBridge emits ask_question
  -> wait Redis BLPOP with 1800s timeout
  -> timeout
  -> AskQuestionBridge emits ask_question_timeout via fanout
  -> tool raises TimeoutError
  -> run_agent error handling emits error + stream_closed
```

第一版保持现有 1800 秒超时时间。

## 9. 测试计划

### 9.1 后端单元测试

新增或扩展 `tests/matmaster/core/test_exp.py`：

- `test_build_runtime_registers_ask_question_when_bridge_available`
- `test_build_runtime_hides_ask_question_when_bridge_missing`
- `test_build_runtime_includes_ask_question_for_builtin_star`
- `test_playground_context_declares_interaction_bridge_field`
- `test_agent_run_service_injected_bridge_reaches_exp_runtime`

扩展 `tests/matmaster/tools/builtin/test_ask_question_tool.py`：

- bridge 使用 async event sink
- request 事件和 timeout 事件均通过 event sink 发出
- timeout 事件不在线程函数中直接发送
- `queue.Empty` 在线程函数中转换为 `TimeoutError`
- cancel sentinel 抛出 `asyncio.CancelledError` 且不 emit timeout

### 9.2 后端 API 测试

新增 `ask_question_reply` endpoint 测试：

- 无权限返回 403
- 无 active run 返回 409
- 空 `request_id` 返回 422 或 400
- `answers` 与 `annotations` 同时为空返回 422 或 400
- 正常请求会：
  - broadcast `ask_question_reply`
  - queue 写入 JSON envelope
  - history 写入 `ask_question_reply`
  - 补齐 `task_id` / `invocation_id`

回归 `confirmation_reply`：

- 仍然广播 `confirmation_reply`
- live SSE payload 的 `type` 必须是 `"confirmation_reply"`，覆盖当前默认参数 bug
- 仍然向 queue 写纯文本
- 仍然写历史文本 content

### 9.3 后端 fanout/persistence 测试

扩展 `tests/matmaster/services/test_agent_run_stream.py`：

- AskQuestion request event 应同时进入 live SSE 和 persistence
- persisted content 应与 `_public_content_for_event("ask_question", ...)` 一致
- `ask_question_timeout` 应进入 SSE 和 persistence
- stop/cancel 期间如果已发出 `ask_question`，历史中后续 terminal event 足以让前端清 pending

### 9.4 前端测试

扩展 `tests/chat-evo/interaction.test.ts` 或新增 handler 测试：

- `ask_question` payload 建成 active interaction
- `ask_question_reply` content 为对象时能清掉 active interaction
- 同 request_id 的 optimistic message 与 SSE reply 不重复
- 历史加载时 `ask_question` 后无 reply/timeout/terminal 时恢复 pending
- 历史加载时 `ask_question` 后有 reply/timeout/cancel/error/terminal failure 时清理 pending

### 9.5 手动验证

本地用可控 provider 或测试 hook 触发一次 AskQuestion：

1. 前端出现结构化问答卡片
2. 提交答案后卡片消失，用户答案显示在对话中
3. agent 收到答案并继续输出
4. 刷新页面后不会重复显示已回答卡片
5. 若在问答卡片出现后刷新，未回答卡片能恢复

## 10. 风险与约束

| 风险 | 处理 |
|---|---|
| `AskQuestionTool` 注册后在无 bridge 环境暴露给模型 | 保留并测试 `exposed_to_model=False` 保护 |
| `confirmation_reply` 被重构时行为回归 | 抽 helper 后必须保留 confirmation API 回归测试 |
| live optimistic message 与 SSE reply 重复 | 先依赖现有前端 dedupe，必要时修前端 dedupe，不改后端协议 |
| request_id 不匹配当前等待问题 | 第一版不加 pending key，依赖单 active run 和前端状态；后续如有乱序再加 Redis pending request |
| bridge 改 fanout 后 async/sync 测试适配复杂 | 一次性切换到 async `event_sink(BusEvent)`，不保留 dict shim |
| 历史里出现旧 confirmation 和新 ask_question 两套交互事件 | 前端已有分支处理，保持事件类型清晰即可 |
| stop 时没有 ask_question_reply/timeout | 前端在同 run terminal cancel/failure/error 后清 pending，不新增 ask_question_cancelled 事件 |
| PlaygroundContext bridge 再次退化成隐式动态属性 | 显式声明 `interaction_bridge: Any = Field(default=None, repr=False)` 并加测试 |
| confirmation live SSE 继续错发 ask_question_reply | 去掉或修正 `broadcast_reply` 默认值，endpoint 显式传事件类型并加红测 |

## 11. 交付定义

修复完成后应满足：

- `tool_catalog.build_definitions()` 在生产 run 中包含 `AskQuestion`
- 模型调用 `AskQuestion` 不再得到 `Unknown tool: AskQuestion`
- `ask_question` live SSE 能触发前端 `AskQuestionWizard`
- `POST /ask_question_reply` 成功唤醒阻塞中的 `AskQuestionBridge`
- `AskQuestionTool` 返回的 `ToolResult.payload` 包含 `request_id`、`questions`、`answers`、`annotations`
- `ask_question` 和 `ask_question_reply` 均写入历史
- 刷新恢复符合等待中和已回答两种状态
- 现有 `/confirmation_reply` 行为不变
- `/confirmation_reply` live SSE type 为 `confirmation_reply`
- `PlaygroundContext` 显式声明 `interaction_bridge`
- 后端 focused pytest 与前端 chat-evo tests 通过

## 12. 实施顺序建议

0. 在 `PlaygroundContext` 显式声明 `interaction_bridge` 字段，并验证
   `AgentRunService -> Exp.build_runtime` 能读到同一个 bridge
1. 调整 `AskQuestionBridge` 为 async `event_sink(BusEvent)` API，并让生产路径走
   `fanout.dispatch`
2. 补 `AskQuestionTool` runtime 注册和 model-visible 测试
3. 抽通用 interaction reply helper，修复 `broadcast_reply` 默认事件类型问题，并确保
   `confirmation_reply` 回归不变
4. 新增 `ChatAskQuestionReplyRequest` 和 `/ask_question_reply`
5. 补后端 fanout/persistence/cancel 测试
6. 补前端 handler / dedupe / history terminal 清理测试
7. 做一次真实端到端手动验证
