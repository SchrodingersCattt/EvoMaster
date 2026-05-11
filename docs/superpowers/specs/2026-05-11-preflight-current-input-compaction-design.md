# Preflight Current Input Compaction Design

## 背景

MatMaster 当前的 compact 流程会在 `AgentKernel` 构造完整模型输入之后再做
preflight 检查。也就是说，模型输入已经包含：

```python
[
    SystemMessage(system_prompt),
    *restored_history,
    UserMessage(current_user_request),
]
```

如果正是用户本轮新消息和新附件把上下文推过 compact 阈值，当前
`ContextCompactor` 会把这条新消息也放进 summary 输入。这样会把当前请求从
当前指令降级成旧历史摘要的一部分，并且让 summary LLM 有机会改写、遗漏或弱化
本轮用户约束。

这个设计只修正 preflight compact 的当前请求边界：旧历史进入 summary，本轮
新发送的用户文本和新 attach 的文件保持在当前指令位置，作为当前指令追加到
compact 后的 `UserMessage`。如果 oversized input offloader 已把超大文本改写成
短引用，则 `<current_instruction>` 使用改写后的短引用，原文留在 workspace 文件中。

## 目标

- preflight compact 时，summary LLM 不接收本轮新发送的用户文本。
- preflight compact 时，summary LLM 不接收本轮新 attach 的文件列表。
- compact 后当前 run 仍能看到本轮有效用户文本和新附件。
- 本轮新请求以 `<current_instruction>` 块追加到 compact 后的 `UserMessage`
  末尾。
- 本轮 images 仍保留在 compact 后的 `UserMessage.images`，避免 vision 输入丢失。
- durable checkpoint 不持久化 `<current_instruction>`，避免下一轮恢复时把上一轮
  当前指令误标成当前指令。
- runtime compact 保持现有行为，不引入长期保护所有用户消息的策略。

## 非目标

- 不改变 runtime compact 的 summary / fallback 策略。
- 不改变长期图片 replay 策略。
- 不把历史所有用户消息都排除出 summary。
- 不把旧附件长期重发给模型。
- 不新增前端展示事件。
- 不改变 `history_checkpoint` 对 replay 隐藏、`compaction` 生命周期事件对 replay
  可见的现有 UI 语义。
- 不在本阶段解决超大单条用户输入自身超过上下文窗口的问题；那属于输入外部化或
  拒绝策略。

## 推荐方案

采用 preflight-only current input split。

preflight compact 触发时，把模型输入拆成三部分：

```python
system_message = messages[0]
history_messages = messages[1:-1]
current_user_message = messages[-1]
```

只有当最后一条消息确认为本轮刚追加的 `UserMessage` 时，才启用这个 split。
`history_messages` 用于 summary；`current_user_message` 不进入 summary。

如果 `history_messages` 为空，不执行 preflight current input split，也不调用 summary。
这类首轮单条超大输入交给 `oversized-user-input-offload` 方案处理。这样可以避免
空 history 被送入 summary 后触发 `Cannot compact messages without user or assistant
history`，也避免在首轮把当前请求误当成旧历史压缩。

compact 成功后，当前 run 使用的模型输入为：

```python
[
    system_message,
    UserMessage(
        content=(
            compact_bundle_without_current_instruction
            + "\n\n<current_instruction>\n"
            + current_instruction_text
            + "\n</current_instruction>"
        ),
        images=current_user_message.images,
    ),
]
```

其中 `current_instruction_text` 由本轮有效请求和本轮新附件构造，不直接复用
`current_user_message.content`。

原因是当前 `current_user_message.content` 可能已经包含全局 available attachments
manifest。直接复用它会把旧附件也放进 `<current_instruction>`，与只保护本轮新
attach 文件的边界冲突。

实现上必须构造两条不同的 compact `UserMessage`：

```python
checkpoint_user_msg = UserMessage(content=compact_bundle_without_current_instruction)
runtime_user_msg = UserMessage(
    content=(
        compact_bundle_without_current_instruction
        + "\n\n<current_instruction>\n"
        + current_instruction_text
        + "\n</current_instruction>"
    ),
    images=current_input_context.images,
)

messages[:] = [system_message, runtime_user_msg]
base_snapshot = [checkpoint_user_msg.model_dump(mode="json")]
```

不能继续用 `messages[1].model_dump(...)` 作为 checkpoint base，因为 `messages[1]`
是 runtime message，会带有 `<current_instruction>` 和本轮 images。checkpoint base
必须保持干净，不持久化 `<current_instruction>`，也不持久化 images。

## 当前输入上下文

新增一个 runtime-only 的 current input context，用于 preflight compact。它是被动
run metadata，不是服务能力端口：

```python
@dataclass(frozen=True)
class CurrentInputContext:
    user_text: str
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()
    pre_query_scope_event_id: int | None = None
```

来源是当前 `ChatSendRequest` / 当前 `User/query` 事件，而不是从全会话
`attachment_manifest` 反推。

`pre_query_scope_event_id` 的来源采用写入前 snapshot 方案：

1. `prepare_send_message()` 在写入当前 User/query 事件之前，先通过 events service
   查询当前 scope 最新 event id。
2. 查询结果记为 `pre_query_scope_event_id`，挂到 `SendStreamContext`。
3. `AgentRunService.run_agent()` 把它放入 `pg_ctx.run_meta["current_input_context"]`。
4. kernel / compactor 只使用这个值作为 preflight checkpoint override。

选择这个方案的原因是它不要求 `add_event()` 改成返回插入 id，也不需要新增
`get_latest_scope_event_id_before(current_query_event_id)` 这类 DAO 方法。代价是
`prepare_send_message()` 多一次读查询，但边界清晰：这个值天然表示当前 query 写入
之前的 scope 末端。

当前实现中，`prepare_send_message()` 已经把当前请求写成 User/query 事件，并在
payload 中携带：

- `content`
- `files`
- `images`
- `workspace_paths`
- `task_id`
- `invocation_id`

设计上应在 `SendStreamContext` 和 `AgentRunService` 的 run metadata 中保留同一份
runtime-only current input context。它只用于本次 run 的 compact 构造，不作为新的
持久化数据源，也不新增 RuntimePorts 字段。

`pg_ctx.run_meta["current_input_context"]` 还必须继续透传到 `AgentRuntimeSpec.meta`。
这是当前 checkout 中最容易遗漏的闭环点：`AgentRunService` 只能把 context 注入
`PlaygroundContext.run_meta`，而 `AgentKernel` 实际读取的是 `spec.meta`。因此
`Exp.build_runtime()` 组装 kernel spec 时必须显式复制该字段。否则 API / Worker
链路虽然已经捕获了 current input context，preflight compactor 仍然会看到
`current_input_context=None`，从而回退到旧的全量 summary 行为。

`user_text` 的最终值由 kernel 在构造当前 `UserMessage` 时确定。若
`oversized-user-input-offload` 已经把 `task` 改写成短引用文本，则
`current_input_context.user_text` 也必须使用改写后的 `task`，不能使用
`prepare_send_message()` 落库的原始超大文本。实现方式可以是在 kernel 内部用
`dataclasses.replace(current_input_context, user_text=task)` 生成本次 compaction 使用
的 effective context。这样 `<current_instruction>` 与 provider 实际看到的当前请求
保持一致，不会把 offloader 刚外部化的大文本重新塞回 prompt。

## Compactor 接口契约

`ContextCompactor.apply_compaction_plan()` 增加可选参数：

```python
def apply_compaction_plan(
    self,
    plan: CompactionPlan,
    messages: list[ChatMessage],
    *,
    current_input_context: CurrentInputContext | None = None,
) -> CompactionResult:
    ...
```

`plan_preflight_compaction(messages)` 仍只负责 token 估算和策略选择，不接收
`current_input_context`。是否启用 current input split 是 apply 阶段的行为。

`AgentKernel` 在调用 apply 时显式传入：

```python
result = compactor.apply_compaction_plan(
    plan,
    state.messages,
    current_input_context=effective_current_input_context,
)
```

runtime compact 调用不传该参数，因此保持现有行为。

## `<current_instruction>` 内容格式

`<current_instruction>` 使用稳定文本块：

```text
<current_instruction>
用户本轮有效请求文本。

[Current attachments]
file_1 example.cif https://oss.example.com/example.cif
workspace_1 /share/project/input/POSCAR
</current_instruction>
```

规则：

- 用户文本为空但本轮有 `files` / `workspace_paths` / `images` 时，仍写
  `<current_instruction>`，块内只列附件信息。
- 用户文本为空且本轮也没有任何附件时，跳过 current input split。
- `files` 按当前请求顺序列出，使用 basename 作为可读名称。
- `workspace_paths` 按当前请求顺序列出。
- `images` 在文本块里列出 URL 作为可读索引，同时保留在 `UserMessage.images`。
  模型视觉输入依赖 content parts，文本 URL 只作为可读附件索引。
- 只包含当前请求中的附件，不包含旧 query 的附件。
- 不把 `<current_instruction>` 放进 `system_prompt` 或 `SystemMessage`。

启用 current input split 时，`build_compact_bundle()` 的
`<continuation_instruction>` 应改成 forward pointer，例如：

```text
<continuation_instruction>
不要向用户复述上述摘要。当前用户指令位于下面的
<current_instruction> 块中；请基于摘要背景直接执行该指令。
</continuation_instruction>
```

这样 `<previous_session_summary>` 负责旧上下文，`<current_instruction>` 负责本轮
指令，不让 continuation 文本和 current instruction 形成两个模糊的当前任务来源。

## Checkpoint 边界

preflight compact 有两份结果：

1. 当前 run 立即使用的 `messages`
2. 持久化到 `history_checkpoint.base_messages` 的 checkpoint base

当前 run 的 `messages` 包含 `<current_instruction>`。checkpoint base 不包含
`<current_instruction>`。

checkpoint base 只包含：

```python
[
    UserMessage(previous_session_summary_bundle_without_current_instruction)
]
```

这条 checkpoint base `UserMessage.images` 必须为空。否则下一轮 restore 时，checkpoint
base 中的 images 会和 tail events 里的同一轮 User/query images 再次相邻合并；
`_merge_user_messages()` 当前会直接拼接 images，没有去重能力，最终会把同一批图片
重复送给 vision provider。

checkpoint 的 `covered_until_event_id` 应覆盖到当前 query 之前，而不是覆盖当前
query。这样下一轮恢复时，当前 query 会作为 checkpoint 后的 tail event 正常恢复：

```python
[
    UserMessage(previous_session_summary_bundle),
    UserMessage(current_query_from_tail_events),
    AssistantMessage(answer_after_current_query),
]
```

provider 前已有相邻 `UserMessage` canonicalization，会把相邻 user 消息合并成单条
provider 输入，不破坏 OpenAI-compatible 消息序列。

为支持这个边界，checkpoint sink 需要能接收可选的覆盖边界 override：

```python
checkpoint_sink(
    payload={
        "durability": "durable",
        "strategy": "summary",
        "covered_until_event_id": pre_query_scope_event_id,
    },
    base_messages=checkpoint_base_snapshot,
)
```

如果 payload 没有提供 `covered_until_event_id`，sink 仍保持当前行为：flush
persistence 后查询最新 scope event id。这保证 runtime compact 不受影响。

### Rehydrated context 的上界

preflight current input split 不能只调整 checkpoint 的 `covered_until_event_id`，还必须
调整 compact bundle 内的 rehydrated attachment 范围。

原因是当前 User/query 已经在 `prepare_send_message()` 阶段落库。`CompactionRehydrator`
如果只按“上一个 checkpoint 之后”过滤附件，就会把本轮 query 的附件也放进
`<rehydrated_context><attachments>...`。这会破坏 checkpoint 语义：checkpoint
声明只覆盖到当前 query 之前，但 base snapshot 内容已经包含当前 query 之后的附件。

为此，attachment manifest 需要支持事件范围过滤：

```python
filter_entries_in_event_range(
    entries,
    after_id=latest_checkpoint_covered_until_event_id,
    until_id=pre_query_scope_event_id,
)
```

规则：

- `after_id` 仍表示排除已经被上一 checkpoint 覆盖的旧附件。
- `until_id` 是可选上界；preflight current split 启用时设为
  `current_input_context.pre_query_scope_event_id`。
- 设置任一边界时，`source_event_id is None` 的 entry 不能进入结果，因为无法证明它
  属于 checkpoint 范围内。
- 非 current split 路径不传 `until_id`，保持现有 runtime compact 行为。

`CompactionRehydrator.build()` 因此增加可选参数：

```python
async def build(self, *, until_event_id: int | None = None) -> str:
    ...
```

preflight current split 路径调用：

```python
rehydrated = await self._rehydrator.build(
    until_event_id=current_input_context.pre_query_scope_event_id
)
```

当前请求的附件只应出现在 `<current_instruction>` 和 tail event 恢复路径中，不应提前
进入 checkpoint base 的 rehydrated context。

关联类型和调用点必须同步更新：

- `matmaster/types/runtime_ports.py`：
  `CompactionCheckpointPayload` 增加
  `covered_until_event_id: NotRequired[int]`。
- `matmaster/types/runtime_ports.py`：
  `CheckpointSink` 协议文档说明 payload 可携带覆盖边界；没有覆盖边界时 sink 自行
  查询 latest scope id。
- `matmaster/core/agent.py`：kernel 在 checkpoint payload 中透传
  `CompactionResult.checkpoint_covered_until_event_id`。
- `matmaster/core/context_compactor.py`：`CompactionResult` 增加
  `checkpoint_covered_until_event_id: int | None`，仅 preflight current input split
  成功且边界可用时填值。
- `src/services/history_checkpoint_service.py`：sink 优先使用
  `payload["covered_until_event_id"]`；只有 payload 未提供时才 fallback 到
  `get_latest_scope_event_id(session_id, spawn_id)`。
- `matmaster/core/exp.py`：`Exp.build_runtime()` 将
  `run_meta["current_input_context"]` 复制到 `AgentRuntimeSpec.meta`。
- `matmaster/manifests/attachment.py`：新增 attachment event-id 范围过滤。
- `matmaster/manifests/rehydrator.py`：`CompactionRehydrator.build()` 支持
  `until_event_id`，并在 attachment manifest 构造时使用范围过滤。
- `tests/matmaster/types/test_runtime_ports.py`：补充 payload 带
  `covered_until_event_id` 的协议 case。
- `tests/matmaster/core/test_agent_kernel_compaction.py`：所有 fake sink 接受新 payload
  键，并断言 preflight split 路径传入的是 query 写入前的 snapshot。

## 数据流

1. API `POST /chat/sessions/{session_id}/stream` 收到当前请求。
2. `prepare_send_message()` 在写入当前 User/query 之前查询最新 scope event id，
   记录为 `pre_query_scope_event_id`。
3. `prepare_send_message()` 写入当前 User/query 事件，包含本轮文本和本轮附件字段。
4. `prepare_send_message()` 把 `pre_query_scope_event_id` 和本轮输入字段挂到
   `SendStreamContext.current_input_context`。
5. Worker 执行 `AgentRunService.run_agent()`。
6. `HistoryRestoreService.restore_history()` 恢复旧历史，并排除当前 `task_id` 的事件。
7. `AgentRunService` 将 `SendStreamContext.current_input_context` 放入
   `pg_ctx.run_meta["current_input_context"]`，作为 runtime-only passive metadata。
8. `Exp.build_runtime()` 将 `pg_ctx.run_meta["current_input_context"]` 复制到
   `AgentRuntimeSpec.meta["current_input_context"]`。
9. `AgentKernel` 先执行 oversized input offloader；如果 task 被改写，生成
   effective current input context，使 `user_text` 等于改写后的 task。
10. `AgentKernel` 用 effective task 与 attachment manifest 构造当前 `UserMessage`。
11. preflight compact 估算超过阈值。
12. 如果 current input split 启用且 `history_messages` 非空，compactor 总结
   `history_messages`，不总结当前 `UserMessage`。
13. compactor 用 `pre_query_scope_event_id` 作为 rehydrated attachment 上界，只补回
   当前 query 之前的 attachment manifest。
14. compactor 构造：
   - runtime compact message：包含 `<current_instruction>`
   - checkpoint base snapshot：不包含 `<current_instruction>`，也不包含 images
   - `checkpoint_covered_until_event_id=pre_query_scope_event_id`
15. checkpoint sink 写入 `history_checkpoint`，覆盖边界为当前 query 之前。
16. `compaction complete` 事件继续正常发给 SSE 和 persistence。
17. 当前 run 继续执行，provider 看到旧历史摘要和当前精确指令。

## 失败处理

- 如果 preflight summary 失败，仍沿用现有行为：抛出异常，不做 runtime fallback。
- 如果 current input context 缺失，preflight compact 回退到现有行为，但应记录 warning。
  这个分支只用于兼容旧调用路径；正常 `POST /stream` 路径必须提供 context。
- 如果 current input context 存在但 `history_messages` 为空，跳过 preflight compact，
  返回 `plan=None`，把首轮单条超大输入交给 offloader 处理。
- 如果 current input context 的用户文本为空且本轮没有任何附件，跳过 current input
  split。
- 如果当前 input split 已启用，但无法计算当前 query 之前的
  `covered_until_event_id`，不得退回使用最新 scope event id 写 checkpoint。否则
  checkpoint 会覆盖当前 query，而 checkpoint base 又不包含 `<current_instruction>`，
  下一轮恢复会丢失当前 query。正确降级是当前 run 继续使用
  `<current_instruction>`，但本次 compact 不写 durable checkpoint。实现上将
  `CompactionResult.durability` 降为 `"ephemeral"`，并复用
  `CompactionResult.failure_reason` 写入可读原因，例如
  `preflight_current_input_boundary_missing`。这样 kernel 现有
  `should_checkpoint = result.durability == "durable"` 逻辑会自然跳过 sink，同时
  `CompactionEvent` 中体现 `checkpoint_written=False` 和边界缺失原因。
- checkpoint 写入失败时，沿用现有行为：`compaction complete` 仍发送，但
  `checkpoint_written=False` 且带 `failure_reason`。

## 测试计划

- preflight compact 触发时，summary provider 收不到当前用户文本。
- preflight compact 触发时，summary provider 收不到当前 `files` / `workspace_paths`
  文本。
- `Exp.build_runtime()` 能把 `pg_ctx.run_meta["current_input_context"]` 透传到
  `runtime.spec.meta["current_input_context"]`。
- preflight current split 触发时，rehydrated attachments 不包含
  `pre_query_scope_event_id` 之后的当前 query 附件。
- `history_messages` 为空时，preflight planner / kernel guard 短路为 `plan=None`，
  不调用 summary。
- compact 后当前 run 的 `UserMessage.content` 包含 `<current_instruction>` 和当前
  有效用户文本。
- compact 后当前 run 的 `<current_instruction>` 只包含当前请求附件，不包含旧附件。
- compact 后当前 run 的 `UserMessage.images` 保留当前 images。
- 构造 5 条历史附件和 1 条本轮附件，断言 `<current_instruction>` 只包含本轮那
  1 条。
- runtime `UserMessage` 带 images，checkpoint base `UserMessage` 不带 images。
- checkpoint base snapshot 不包含 `<current_instruction>`。
- checkpoint sink payload 的 `covered_until_event_id` 等于
  `prepare_send_message()` 写当前 User/query 前 snapshot 出来的 scope event id。
- 下一轮 restore 从 checkpoint base 加 tail events 恢复当前 query。
- 下一轮 restore + canonicalize 后，provider 侧相邻 `UserMessage` 被合并，不破坏
  OpenAI-compatible 的 system/user/assistant 顺序。
- rehydrator 使用事件范围过滤附件时，不重复列出已经被上一 checkpoint 覆盖的旧
  attachment manifest 条目，也不提前列出当前 query 之后的 attachment manifest 条目。
- `pre_query_scope_event_id` 缺失时，本次 compact 不写 `history_checkpoint` 行，
  `CompactionEvent.checkpoint_written=False`，`failure_reason` 可读。
- runtime compact 不启用 current input split，现有测试保持通过。
- replay 继续隐藏 `history_checkpoint`，保留公开 `compaction` 生命周期事件。

## 实施决策

- current input context 使用窄 frozen dataclass，字段为 tuple，避免 mutable default。
- current input context 作为 passive run metadata 传递，不新增 RuntimePorts 能力端口。
- `pre_query_scope_event_id` 在 `prepare_send_message()` 写当前 User/query 前 snapshot，
  并随 `SendStreamContext` 进入 `AgentRunService` / `pg_ctx.run_meta`。
- `Exp.build_runtime()` 是 run metadata 到 kernel metadata 的唯一透传点；必须把
  `current_input_context` 纳入 `AgentRuntimeSpec.meta`。
- `apply_compaction_plan()` 增加可选 `current_input_context` 参数；planner 阶段不接收
  该参数。
- preflight current input split 成功时，必须分别构造 runtime user message 与
  checkpoint user message。
- preflight current input split 构造 rehydrated context 时使用
  `pre_query_scope_event_id` 作为 attachment 上界；当前 query 附件只进入
  `<current_instruction>` 和后续 tail-event restore。
- checkpoint payload 增加可选 `covered_until_event_id` override；runtime compact
  未提供该键时保持 sink 现有 latest-scope fallback。
- 与 oversized input offloader 的顺序固定为 offloader 在前，current input split 在后；
  `<current_instruction>` 使用 offloader 改写后的 task 文本。
- `<current_instruction>` 文本中列出 files、workspace paths、images；同时 images 继续
  保留在 `UserMessage.images`。
