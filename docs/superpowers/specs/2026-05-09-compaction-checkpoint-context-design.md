# Compaction Checkpoint Context Design

## 背景

当前 MatMaster 已经有一套上下文压缩基础设施：

- `ContextCompactor` 在 preflight 和 runtime 阶段判断是否需要压缩。
- `CompactionEvent` 对外表达压缩生命周期，包含 `running`、`complete`、
  `interrupted` 等状态。
- `history_checkpoint` 持久化压缩后的恢复基线，包含
  `covered_until_event_id` 和 `base_messages`。
- `HistoryRestoreService` 优先从最新有效 checkpoint 恢复历史，再拼接
  checkpoint 之后的 tail events。
- replay 路径会隐藏 `history_checkpoint`，但保留公开的 `compaction` 生命周期事件。

这套机制已经足以承担 compact boundary 的职责，不需要新增
`compact_boundary` 事件。需要修正的是压缩后的模型输入语义。

当前实现存在一个严重设计问题：压缩摘要被构造成
`SystemMessage("[Compacted Context]\n<summary text>")`。虽然它没有直接拼进
`system_prompt` 字符串，但它仍然使用了 system role。压缩摘要属于旧对话状态，
不是系统规则；把它放到 system role 会把动态历史提升到高权限层级，容易覆盖当前
用户请求，也会让旧工具输出、旧用户临时要求或不完全可靠的摘要拥有错误的优先级。

本设计将压缩后的上下文视为上一轮对话 session 的文本背景。压缩后进入模型的
基础上下文不再保留旧 transcript 的 role 序列，而是开启一个新的模型会话，由用户
消息提供上一轮对话的摘要和系统恢复出的外部上下文。

## 目标

- 保留 `history_checkpoint` 作为唯一 durable compaction 边界。
- 禁止压缩摘要进入 `system_prompt` 或任何 `SystemMessage`。
- 将压缩后的基础模型输入收敛为：

```python
[
    SystemMessage(system_prompt),
    UserMessage(previous_session_context_bundle),
]
```

- 将 summary、附件、已加载 skill、MCP 状态、Bohrium 状态、workspace 路径、
  artifact 路径等回填信息合并进同一条 `UserMessage`，避免相邻 user message。
- 把压缩结果表达成上一轮对话的内容摘要，而不是半截旧消息历史。
- 保证新写入的 `history_checkpoint.base_messages` 不包含 `SystemMessage`。
- 支持旧 checkpoint 兼容读取，旧的 compacted `SystemMessage` 在恢复时降级转换为
  `UserMessage`。
- 为后续工具结果预算、prompt-too-long retry、手动 compact 和 rehydration 扩展留下
  清晰边界。

## 非目标

- 不新增 `compact_boundary` 事件。
- 不改变 UI replay 保留完整 transcript 和 `compaction` 生命周期事件的目标。
- 不在本阶段实现 partial compact。
- 不在本阶段要求前端展示 `history_checkpoint`。
- 不把压缩摘要当作事实数据库或系统规则。
- 不默认保留 `initial_task_msg` 原文。
- 不默认保留最近若干 turn 原文。
- 不依赖 provider 自动合并相邻 user message。

## 设计原则

### history_checkpoint 是唯一 durable 边界

`history_checkpoint.covered_until_event_id` 表示 checkpoint 覆盖到哪条业务事件。
下一轮恢复时，模型历史由两部分组成：

1. `history_checkpoint.base_messages`
2. `covered_until_event_id` 之后的业务 tail events

`compaction` 事件只负责 UI 和调试层面的生命周期表达，不参与模型历史恢复。

### system_prompt 只承载静态规则

`system_prompt` 只来自 `ContextBuilder`，用于描述 MatMaster 的身份、工具协议、
安全边界和运行规则。压缩摘要、附件恢复、skill 状态、MCP 状态、Bohrium 状态都属于
动态上下文，不能进入 system 层。

### 压缩后是新 session 的第一条用户上下文消息

压缩后的信息应从文本对话角度表达为：

```text
以下是上一轮对话的内容摘要。现在是一个新的对话 session，请把这些信息作为历史背景继续当前任务。
```

这让模型把压缩内容当作普通用户提供的背景材料，而不是继承旧 transcript 中的
多轮 role 结构。

### summary 与 rehydrated context 合并为单条 UserMessage

许多 LLM API 或兼容路由不允许相邻两个 `UserMessage`，或者对相邻同 role 消息的
合并语义不稳定。因此 MatMaster 必须在内部保证压缩后的基础上下文只使用一条
`UserMessage` 承载所有恢复信息。

### 有价值的信息必须进入 summary 或回填块

compact 后不保留 `initial_task_msg` 和 recent raw turns。旧历史里仍然有价值的信息
必须进入 summary 或 rehydrated context，而不是以原始 message 形式继续留在模型输入里。

## 压缩后模型输入

压缩刚完成并继续当前 run 时，模型基础输入应为：

```python
[
    SystemMessage(system_prompt),
    UserMessage(
        "以下是上一轮对话的内容摘要。现在是一个新的对话 session，请把这些信息作为历史背景继续当前任务。"
        "<previous_session_summary>用户目标、已完成工作、关键路径和下一步。</previous_session_summary>"
        "<rehydrated_context>附件、已加载 skill、MCP、Bohrium 与 artifact 索引。</rehydrated_context>"
        "<current_execution_state>当前 run 中压缩发生时的继续点。</current_execution_state>"
        "<continuation_instruction>不要复述摘要，直接继续任务。</continuation_instruction>"
    ),
]
```

下一轮用户发送新请求时，如果从 checkpoint 恢复出的历史最后一条是 compact
`UserMessage`，且 kernel 即将追加当前 `UserMessage(task)`，最终发给 provider 前必须合并为：

```python
[
    SystemMessage(system_prompt),
    UserMessage(
        previous_session_context_bundle
        + "<current_user_request>用户本轮新请求文本。</current_user_request>"
    ),
]
```

如果 checkpoint 后已有 assistant/tool tail events，则最终序列可以是：

```python
[
    SystemMessage(system_prompt),
    UserMessage(previous_session_context_bundle),
    AssistantMessage(content="checkpoint 后的已接受 assistant 回复"),
    ToolMessage(tool_call_id="call_1", tool_name="Read", content="checkpoint 后的工具结果"),
    UserMessage(new_request),
]
```

但 provider 前仍必须运行消息规范化，确保不存在非法相邻 user message，也不破坏
tool call 和 tool result 配对。

## UserMessage 内容格式

建议压缩后的 user content 使用稳定标签块：

```text
以下是上一轮对话的内容摘要。现在是一个新的对话 session，请把这些信息作为历史背景继续当前任务。

<previous_session_summary>
这里放压缩摘要。摘要必须覆盖用户目标、关键约束、已完成工作、重要文件路径、工具调用结果、错误与修复、当前状态、待办事项和下一步。
</previous_session_summary>

<rehydrated_context>
<attachments>
- 用户上传文件、图片或其它附件。
</attachments>

<loaded_skills>
- 已加载或已命中的 skill，以及继续任务必须知道的使用约定。
</loaded_skills>

<active_tools>
- active MCP server、已发现工具、延迟加载工具或 agent 列表。
</active_tools>

<runtime_context>
- workspace、execution_workdir、remote_workdir、Bohrium project/session/job 状态。
</runtime_context>

<external_artifacts>
- 大工具结果外部化后的文件路径、日志路径、表格路径和读取方式。
</external_artifacts>
</rehydrated_context>

<current_execution_state>
如果压缩发生在当前 run 中，记录当前用户请求、已经执行到哪里、下一步应继续什么。
</current_execution_state>

<continuation_instruction>
不要向用户复述上述摘要，除非用户明确要求。请直接基于这些背景继续完成当前任务。
</continuation_instruction>
```

`current_execution_state` 和未来的 `current_user_request` 只在需要时出现。标签块可以为空缺省，
但不应写无意义占位内容。

## history_checkpoint 合同

新写入的 `history_checkpoint.content` 继续保留核心字段：

```json
{
  "covered_until_event_id": 42,
  "base_messages": [
    {
      "role": "user",
      "content": "以下是上一轮对话的内容摘要。现在是一个新的对话 session，请把这些信息作为历史背景继续当前任务。"
    }
  ],
  "reason": "summary"
}
```

建议扩展元数据字段：

```json
{
  "schema_version": 2,
  "compaction_id": "task-1:root:1",
  "phase": "runtime",
  "strategy": "summary",
  "durability": "durable",
  "trigger_tokens": 167842,
  "context_limit": 200000,
  "reserved_summary_tokens": 20000,
  "auto_compact_buffer_tokens": 13000,
  "retained_turns": 0,
  "messages_before": 96,
  "messages_after": 2,
  "summary_message_role": "user"
}
```

其中恢复路径只依赖 `covered_until_event_id` 和 `base_messages`。其它字段用于诊断和未来演进。

新 checkpoint 写入规则：

- `base_messages` 不得为空。
- `base_messages` 通常只包含一条 `UserMessage`。
- `base_messages[0]` 必须是 compact context bundle。
- `base_messages` 不得包含任何 `SystemMessage`。
- `base_messages` 必须能接在当前 run 的 `SystemMessage(system_prompt)` 后通过 provider 消息校验。

旧 checkpoint 兼容规则：

- 如果读取到旧格式 `SystemMessage("[Compacted Context]\n<summary text>")`，恢复时转换为
  `UserMessage(previous_session_context_bundle)`。
- 新写入不再产生旧格式。
- 如果旧 checkpoint 无法转换或转换后校验失败，继续尝试更旧 checkpoint，再 fallback 到完整事件历史。

## 压缩流程

### 当前 run 内自动压缩

```text
AgentKernel 每轮 LLM 前
  -> 估算当前 messages token
  -> 触发 compact
  -> 发 CompactionEvent(status=running)
  -> 对 SystemMessage 之外的有效上下文做摘要
  -> 构造 previous_session_context_bundle
  -> 回填附件、skill、MCP、Bohrium、workspace、artifact 等上下文到同一 bundle
  -> state.messages = [SystemMessage(system_prompt), UserMessage(bundle)]
  -> 若 durable:
       flush persistence barrier
       get_latest_scope_event_id()
       add_history_checkpoint(base_messages=[UserMessage(bundle)])
  -> 发 CompactionEvent(status=complete)
  -> 用压缩后的 state.messages 继续当前 LLM 请求
```

摘要输入应包含除当前 `SystemMessage(system_prompt)` 外的所有有效上下文。已有 compact
summary、tail messages、当前用户请求和当前执行状态都应被纳入新的 summary。这样多次
compact 是状态快照的迭代压缩，而不是丢失上一轮摘要。

### 下一轮恢复

```text
HistoryRestoreService.restore_history
  -> get_history_checkpoints(session_id, spawn_id, limit=5)
  -> 取最新可验证 checkpoint
  -> 反序列化 base_messages
  -> 兼容转换旧 compacted SystemMessage
  -> get_scope_events_after_id(covered_until_event_id)
  -> 排除 history_checkpoint / compaction / context_compaction
  -> 排除当前 task_id 的 in-flight events
  -> tail events 转 Message
  -> 拼接 base_messages + tail_messages
  -> trim history images
  -> validate tool sequence
  -> 返回给 AgentKernel
```

`AgentKernel` 在追加当前用户请求前，必须通过消息规范化避免相邻 user message。对于 compact
context bundle 与当前用户请求相邻的场景，应合并成同一条 `UserMessage`。

## 组件设计

### ContextCompactor

职责保留为触发判断、摘要生成和压缩结果构造，但输出形态改为 compact context bundle。

关键变化：

- 不再构造 `SystemMessage("[Compacted Context]\n<summary text>")`。
- 不再保留 `initial_task_msg` 原文。
- 不再保留 recent turns 原文。
- 压缩后的 `state.messages` 只保留当前 `SystemMessage(system_prompt)` 和一条
  `UserMessage(bundle)`。
- `base_snapshot` 只包含 `UserMessage(bundle)`。

### CompactContextFormatter

新增小组件，负责把 summary、rehydrated context、current execution state 等组合成稳定文本。

建议接口：

```python
class CompactContextFormatter:
    def format(
        self,
        *,
        summary: str,
        rehydrated_context: str | None,
        current_execution_state: str | None,
        continuation_instruction: str,
    ) -> str:
        return formatted_compact_context
```

这个组件只负责文本格式，不读取 DB、不调用工具、不估算 token。

### CompactionRehydrator

后续增强组件，负责从外部状态生成回填上下文。第一阶段可以返回空字符串，之后逐步接入：

- 用户附件和图片。
- 已加载或已命中的 skill。
- active MCP server 和工具清单。
- Bohrium runtime、job registry、remote workdir。
- workspace 与重要 artifact 路径。
- 大工具结果外部化索引。

输出必须是文本片段，最终由 `CompactContextFormatter` 合并到同一条 `UserMessage`。

### HistoryCheckpointCodec

职责扩展为 checkpoint base messages 的版本兼容与校验。

新规则：

- 新写入禁止 `SystemMessage`。
- 读取旧 checkpoint 时允许兼容转换。
- 校验最终发送给 provider 的消息序列，而不是假设 checkpoint 第一条必须是 system。

### Message canonicalization

在 provider 调用前增加或强化消息规范化：

- 合并相邻 `UserMessage`。
- 合并 compact context 与当前 user request 时使用 `<current_user_request>` 标签。
- 不破坏 assistant tool_calls 与 tool result 的连续配对。
- 保持 multimodal user content 在同一条 user message 内。

## 触发阈值建议

当前默认 `context_limit=200000`、`trigger_ratio=0.9`，阈值为 180000 token。建议改为
有效上下文窗口模型：

```text
auto_threshold = context_limit - reserved_summary_tokens - auto_compact_buffer_tokens
```

默认建议：

```text
context_limit = 200000
reserved_summary_tokens = 20000
auto_compact_buffer_tokens = 13000
auto_threshold = 167000
```

`trigger_ratio` 可保留用于兼容旧配置，但新逻辑应优先使用显式预算字段。这样可以给 compact
请求本身和 summary 输出留出空间。

## 工具结果预算

工具结果预算不是第一阶段的必要条件，但应作为后续重要改进。

目标是在 compact 前或 LLM 调用前处理超大 `ToolMessage`：

- 完整结果写入 `.matmaster/tool-results/{task_id}/{call_id}.*`。
- 模型上下文中只保留预览、状态、大小和可读取路径。
- artifact 路径进入 compact bundle 的 `<external_artifacts>`。
- 不通过无痕截断丢掉科研任务所需的可追溯信息。

## prompt too long retry

当 compact summary 请求本身因为 prompt too long 失败时，不应直接放弃。建议按 turn 或完整
tool-call group 从最旧部分开始丢弃后重试：

```text
第 1 次：完整上下文摘要
第 2 次：丢弃最旧约 20% group
第 3 次：继续丢弃最旧约 20% group
仍失败：
  runtime -> fallback 到 ephemeral sliding window 或错误事件
  preflight -> 按产品策略选择错误事件或 ephemeral fallback
```

即使发生有损重试，最终写入 durable checkpoint 的前提仍是生成了合法 compact bundle。
如果只能 fallback 到 ephemeral 策略，则不写 checkpoint，并在 `CompactionEvent.complete` 中标记
`checkpoint_written=False`。

## 测试计划

### 单元测试

- `ContextCompactor` 压缩后只保留：
  - 当前 `SystemMessage(system_prompt)`
  - 一条 `UserMessage(compact bundle)`
- 新 `base_snapshot` 不包含 `SystemMessage`。
- compact bundle 包含 `<previous_session_summary>`。
- compact bundle 可包含 `<rehydrated_context>` 和 `<current_execution_state>`。
- 不再保留 `initial_task_msg` 原文和 recent raw turns。
- 多次 compact 时，第二次 summary 输入包含第一次 compact bundle。
- compact summary provider 返回 `<analysis>` 和 `<summary>` 时，只保存 summary。

### checkpoint 测试

- 新写入 checkpoint 的 `base_messages[0].role == "user"`。
- 新写入 checkpoint 拒绝任何 `SystemMessage`。
- 旧 checkpoint 中 `SystemMessage("[Compacted Context]\n<summary text>")` 可恢复为 `UserMessage`。
- 无效 checkpoint 继续尝试更旧 checkpoint。
- `covered_until_event_id` 后的 `compaction` 和 `history_checkpoint` 不进入 tail history。

### provider 消息校验测试

- compact context + 当前 user request 相邻时合并为单条 `UserMessage`。
- 合并后保留当前用户请求文本。
- 合并不破坏图片或其它 user content parts。
- assistant tool_calls 与 tool results 的配对仍然合法。

### 集成测试

- 自动 compact 后继续当前 run，下一次 LLM 调用能收到新结构。
- run 完成后 DB 中存在 `compaction running`、`history_checkpoint`、`compaction complete`。
- replay 显示 `compaction` 生命周期，不显示 `history_checkpoint`。
- 下一轮恢复从 checkpoint base bundle 加 tail events 继续。
- 子 agent checkpoint 按 `spawn_id` 隔离。

## 迁移策略

1. 先支持读取旧 checkpoint 并转换为 user bundle。
2. 再切换新 checkpoint 写入格式。
3. 保留 `history_checkpoint.content.reason` 和 `covered_until_event_id` 的旧字段兼容。
4. 新增 `schema_version=2` 只作为诊断字段，不作为恢复硬依赖。
5. 观察线上 checkpoint 恢复失败日志，确认旧数据兼容稳定后，再移除旧格式写入路径。

## 风险与应对

### 风险：summary 遗漏旧 recent turns 的关键信息

压缩后不再保留 raw recent turns，因此 summary prompt 必须明确要求保存当前请求、最近执行状态、
下一步和关键工具结果。测试中应覆盖当前 run 中间 compact 的场景。

### 风险：相邻 user message 合并破坏多模态输入

合并逻辑必须支持文本和图片 content parts。对于图片，应该保持在同一条 user message 内，而不是
丢弃或拆成新消息。

### 风险：旧 checkpoint 兼容转换不完整

读取旧 checkpoint 时，只对明确的 compacted system message 做转换。其它 system message 不应静默
吞掉，应视为无效 checkpoint 并尝试更旧版本。

### 风险：bundle 太长

rehydrated context 必须有预算。大型附件、工具结果和文件内容应外部化为 artifact 路径，只在 bundle
中保留索引和摘要。

## 成功标准

- 压缩后 provider 输入中只有一条 system role 消息，且它只来自当前 `system_prompt`。
- 压缩摘要和回填上下文全部位于同一条 user role 消息。
- 新 checkpoint 不再写入 compacted `SystemMessage`。
- 下一轮恢复不会生成相邻 user message。
- UI replay 行为保持不变：用户能看到压缩生命周期，看不到 checkpoint 内部 payload。
- durable checkpoint 仍然能通过 `covered_until_event_id` 恢复 tail events。
- 多次 compact 不丢失上一轮 compact summary 的有效状态。
