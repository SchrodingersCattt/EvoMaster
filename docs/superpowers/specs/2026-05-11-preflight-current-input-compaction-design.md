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
新发送的用户文本和新 attach 的文件保持原样，作为当前指令追加到 compact 后的
`UserMessage`。

## 目标

- preflight compact 时，summary LLM 不接收本轮新发送的用户文本。
- preflight compact 时，summary LLM 不接收本轮新 attach 的文件列表。
- compact 后当前 run 仍能看到本轮新用户文本和新附件。
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

其中 `current_instruction_text` 由本轮原始请求和本轮新附件构造，不直接复用
`current_user_message.content`。

原因是当前 `current_user_message.content` 可能已经包含全局 available attachments
manifest。直接复用它会把旧附件也放进 `<current_instruction>`，与只保护本轮新
attach 文件的边界冲突。

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
```

来源是当前 `ChatSendRequest` / 当前 `User/query` 事件，而不是从全会话
`attachment_manifest` 反推。

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

## `<current_instruction>` 内容格式

`<current_instruction>` 使用稳定文本块：

```text
<current_instruction>
用户本轮原始请求文本。

[Current attachments]
file_1 example.cif https://oss.example.com/example.cif
workspace_1 /share/project/input/POSCAR
</current_instruction>
```

规则：

- 用户文本为空时，不写空占位；但发送消息路径本身要求有非空 content。
- `files` 按当前请求顺序列出，使用 basename 作为可读名称。
- `workspace_paths` 按当前请求顺序列出。
- `images` 在文本块里列出 URL 作为可读索引，同时保留在 `UserMessage.images`。
  模型视觉输入依赖 content parts，文本 URL 只作为可读附件索引。
- 只包含当前请求中的附件，不包含旧 query 的附件。
- 不把 `<current_instruction>` 放进 `system_prompt` 或 `SystemMessage`。

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
        "covered_until_event_id": previous_scope_event_id,
    },
    base_messages=checkpoint_base_snapshot,
)
```

如果 payload 没有提供 `covered_until_event_id`，sink 仍保持当前行为：flush
persistence 后查询最新 scope event id。这保证 runtime compact 不受影响。

## 数据流

1. API `POST /chat/sessions/{session_id}/stream` 收到当前请求。
2. `prepare_send_message()` 写入当前 User/query 事件，包含本轮文本和本轮附件字段。
3. Worker 执行 `AgentRunService.run_agent()`。
4. `HistoryRestoreService.restore_history()` 恢复旧历史，并排除当前 `task_id` 的事件。
5. `AgentRunService` 构造 current input context，并通过 runtime-only metadata 传给
   kernel / compactor。
6. `AgentKernel` 追加当前 `UserMessage`。
7. preflight compact 估算超过阈值。
8. compactor 总结 `history_messages`，不总结当前 `UserMessage`。
9. compactor 构造：
   - runtime compact message：包含 `<current_instruction>`
   - checkpoint base snapshot：不包含 `<current_instruction>`
10. checkpoint sink 写入 `history_checkpoint`，覆盖边界为当前 query 之前。
11. `compaction complete` 事件继续正常发给 SSE 和 persistence。
12. 当前 run 继续执行，provider 看到旧历史摘要和当前精确指令。

## 失败处理

- 如果 preflight summary 失败，仍沿用现有行为：抛出异常，不做 runtime fallback。
- 如果 current input context 缺失，preflight compact 回退到现有行为，但应记录 warning。
- 如果当前 input split 已启用，但无法计算当前 query 之前的
  `covered_until_event_id`，不得退回使用最新 scope event id 写 checkpoint。否则
  checkpoint 会覆盖当前 query，而 checkpoint base 又不包含 `<current_instruction>`，
  下一轮恢复会丢失当前 query。正确降级是当前 run 继续使用
  `<current_instruction>`，但本次 compact 不写 durable checkpoint，并在
  `CompactionEvent` 中体现 `checkpoint_written=False` 和边界缺失原因。
- checkpoint 写入失败时，沿用现有行为：`compaction complete` 仍发送，但
  `checkpoint_written=False` 且带 `failure_reason`。

## 测试计划

- preflight compact 触发时，summary provider 收不到当前用户文本。
- preflight compact 触发时，summary provider 收不到当前 `files` / `workspace_paths`
  文本。
- compact 后当前 run 的 `UserMessage.content` 包含 `<current_instruction>` 和当前
  用户原文。
- compact 后当前 run 的 `<current_instruction>` 只包含当前请求附件，不包含旧附件。
- compact 后当前 run 的 `UserMessage.images` 保留当前 images。
- checkpoint base snapshot 不包含 `<current_instruction>`。
- checkpoint `covered_until_event_id` 使用当前 query 之前的 scope event id。
- 下一轮 restore 从 checkpoint base 加 tail events 恢复当前 query。
- runtime compact 不启用 current input split，现有测试保持通过。
- replay 继续隐藏 `history_checkpoint`，保留公开 `compaction` 生命周期事件。

## 实施决策

- current input context 使用窄 frozen dataclass，字段为 tuple，避免 mutable default。
- current input context 作为 passive run metadata 传递，不新增 RuntimePorts 能力端口。
- `<current_instruction>` 文本中列出 files、workspace paths、images；同时 images 继续
  保留在 `UserMessage.images`。
