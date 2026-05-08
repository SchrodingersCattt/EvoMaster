# Token Usage Events Design

## 背景

当前 agent loop 已经能从 provider 返回中提取 token usage，并在
`AgentKernel` 中累计到 `RunResultEvent.usage`。问题在于 usage 没有形成稳定的
持久化审计链：

- `response` 事件只携带可见文本，不携带 usage。
- `run_result` 内部事件携带 usage，但 public content 持久化时没有包含 usage。
- 历史回放会在已有 `response` 时隐藏同一 `(task_id, spawn_id)` 的 trailing
  `run_result`，因此只把 usage 放在 live `run_result` 顶层不足以支撑回放。
- `tool_result.turn_usage` 可能在同一 LLM turn 的多个 tool result 上重复出现，
  不能作为审计聚合来源。

本设计补齐两个互补层次：

1. 在完整的 `response` 事件中持久化当前已接受 LLM turn 的 usage。
2. 在 `run_result` public content 中持久化整次 run 的终态 usage。

## 目标

- 让历史 DB 中能查询到每个完整 answer segment 对应的单轮 usage。
- 让历史 DB 中能查询到每次 run 的终态累计 usage。
- 保持 live SSE、历史回放、`ChatHistoryConverter` 的文本兼容性。
- 避免 retry 中被丢弃的 incomplete attempt 被误记为 accepted response usage。
- 明确 response 级 usage 与 run_result 级 usage 的不同语义。

## 非目标

- 不实现账单级全量成本统计。retry 失败 attempt、context compaction summary LLM、
  subagent 内部 LLM 消耗等可在后续专门的 token audit 事件中补充。
- 不改变 quota 扣减语义。当前 `use_quota` 仍是成功后扣一次额度，不按 token 扣费。
- 不要求 streaming response chunk 实时携带 usage。provider usage 通常要到 LLM
  turn 结束才可用。
- 不重构 replay dedupe 的整体策略，只做 response content 结构化后的兼容处理。

## 当前链路

Provider 层：

- `OpenAIProvider.chat_stream()` 通过 `stream_options.include_usage=True` 取得
  usage-only chunk。
- `BedrockProvider.chat_stream()` 从 `metadata.usage` 取得 usage。
- Provider 统一输出 `StreamChunk.usage` / `LLMResponse.usage`。

Kernel 层：

- `_stream_llm_items()` 聚合流式文本、reasoning、tool call delta 和最终 usage。
- `_call_llm_streaming()` 包装 retry，并只把 accepted `LLMResponse` 交给 `_run_items()`。
- `_run_items()` 在 accepted response 后执行：
  - `turn_usage = response.usage`
  - `_accumulate_usage(state.total_usage, response.usage)`
  - `state.usage_vendor_by_turn.append(...)`

事件层：

- `AssistantStateEvent` 和 `ToolResultEvent` 已带 `turn_usage` / `total_usage`。
- `RunResultEvent` 已带 `usage` / `usage_vendor_by_turn`。
- `ResponseEvent` 当前不带 usage。

持久化层：

- `PersistenceHandler` 使用 `_public_content_for_event()` 的返回值作为 DB content。
- 当前 `run_result` public content 不包含 usage。
- 当前 `response` 没有专门 mapping，通常回落为字符串 content。

## 设计原则

### response 表达单轮 accepted usage

完整的 `response` 事件表示某次 LLM turn 已形成可见 assistant response。它携带的
usage 只表示这一轮 LLM call，不代表整次 run 的最终成本。

### run_result 表达终态 run usage

`run_result` 是业务终态。它携带的 usage 是 root kernel 已累计的 accepted LLM
turn usage 总和，适合做 run 级查询、展示和粗粒度审计。

### 持久化字段不破坏文本消费

历史 DB 可以把 response content 存为结构化对象，但 replay 到前端时应保持
`content` 为可直接渲染的字符串，同时把 usage 字段提升到 event 顶层。

## 数据模型变更

扩展 `ResponseEvent`：

```python
class ResponseEvent(EventBase):
    type: Literal["response"] = "response"
    content: str = ""
    stream_state: str | None = None
    stream_id: str | None = None
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor: dict[str, Any] | None = None
```

字段语义：

| 字段 | 语义 |
|---|---|
| `turn_index` | kernel turn 序号，即 `state.turn` |
| `turn_usage` | 当前 accepted LLM turn 的 scalar usage |
| `total_usage` | 截止当前 turn 的累计 scalar usage |
| `usage_vendor` | 当前 turn 的 provider-native usage 快照 |

`RunResultEvent` 模型本身无需新增字段。它已经包含：

- `num_turns`
- `usage`
- `usage_vendor_by_turn`
- `finish_detail`

本阶段只扩展 public content mapping。

## 事件生成设计

### streaming response

`stream_state in {"start", "streaming", "end"}` 的 response 继续走当前路径，只用于
live UI 文本流，不携带 usage，也不持久化。

### complete response

`stream_state="complete"` 的 response 应在 accepted `LLMResponse` 被确认后生成，
而不是在 `_stream_llm_items()` 的 `finally` 中生成。

推荐流程：

1. `_stream_llm_items()` 仍负责 streaming 文本事件和最终 `LLMResponse`。
2. `_call_llm_streaming()` 继续负责 retry 判断。
3. `_run_items()` 收到 accepted `LLMResponse` 后：
   - 提取 `turn_usage`
   - 累加 `state.total_usage`
   - append `usage_vendor_by_turn`
   - 生成 `response.complete`，携带当前 turn usage 和累计 usage
   - 继续生成 `AssistantStateEvent` / `ToolCallEvent` / `ToolResultEvent` 或 terminal

这样可以避免 first attempt incomplete response 被 retry 后仍留下完整 response
usage 记录。

### complete response 的发出条件

只在以下条件满足时发出持久化 complete response：

- `response.content` 有有效可见文本。
- 文本不是 trivial response，如只有省略号或空白。
- response 已通过 `_call_llm_streaming()` 的 retry gate，属于 accepted turn。

对于 tool-call turn 中模型先输出少量可见文本再发 tool call 的情况，仍可以发
`response.complete`，但该 turn 后续还会发 `assistant_state` 和 tool events。
审计消费者必须按 `turn_index` 去重，不从多个事件简单求和。

## Public Content Mapping

### response

新增 `_public_content_for_event("response", payload)` 分支。

无 usage 的 response 继续返回字符串：

```json
"answer chunk"
```

有 usage 的 complete response 返回结构化对象：

```json
{
  "content": "answer text",
  "turn_index": 3,
  "stream_id": "turn-12",
  "turn_usage": {
    "prompt_tokens": 1000,
    "completion_tokens": 120,
    "total_tokens": 1120
  },
  "total_usage": {
    "prompt_tokens": 3000,
    "completion_tokens": 500,
    "total_tokens": 3500
  },
  "usage_vendor": {
    "prompt_tokens": 1000,
    "completion_tokens": 120,
    "total_tokens": 1120
  }
}
```

`usage_vendor` 只在 provider 返回时写入；没有则省略或置为 `null`。

### run_result

扩展 run_result public content：

```json
{
  "content": "final answer",
  "status": "completed",
  "reason": "natural",
  "num_turns": 3,
  "usage": {
    "prompt_tokens": 3000,
    "completion_tokens": 500,
    "total_tokens": 3500
  },
  "usage_vendor_by_turn": [
    {"prompt_tokens": 1000, "completion_tokens": 120, "total_tokens": 1120},
    {},
    {"prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980}
  ],
  "finish_detail": null
}
```

兼容性要求：

- 保留现有 `content`、`status`、`reason`、`finish_detail` 字段名。
- 对 legacy `finish` alias 使用相同 shape。
- `usage_vendor_by_turn` 为空时可以省略，减少历史噪声。

## Replay 兼容

DB 中结构化 response content 回放时需要解包：

输入 DB event：

```json
{
  "type": "response",
  "content": {
    "content": "answer text",
    "turn_usage": {"total_tokens": 1120},
    "total_usage": {"total_tokens": 3500}
  }
}
```

Replay SSE 输出：

```json
{
  "type": "response",
  "content": "answer text",
  "turn_usage": {"total_tokens": 1120},
  "total_usage": {"total_tokens": 3500}
}
```

这样前端旧逻辑可以继续按字符串 `content` 渲染，新逻辑可以读取 usage metadata。

`ChatHistoryConverter._assistant_content()` 已支持 dict content 中的 `content` 字段，
但仍应补回归测试，防止未来重构破坏。

## Dedupe 交互

当前 replay dedupe 会在同一 `(task_id, spawn_id)` 已看到 replayable response 后
隐藏 trailing `run_result`，避免最终回答重复出现。

本设计接受这一行为：

- response.complete 携带 turn-level 和截止当前 turn 的 total usage，因此 replay 中
  即使 run_result 被隐藏，前端仍能看到最终 answer 对应 usage。
- DB 中 run_result content 仍持久化终态 usage，供 API 查询和后台审计使用。

后续若需要前端在 replay 中一定看到 run_result usage，可以新增轻量的
`token_usage_snapshot` 事件，而不是破坏 run_result dedupe。

## 子 agent 语义

本设计保持当前 root run 语义：

- root `run_result.usage` 只表示 root kernel accepted LLM turns 的累计 usage。
- subagent 事件通过 `spawn_id` 转发，但 child terminal event 当前不稳定进入
  public persistence path。
- 子 agent 全量成本审计属于后续 token audit 事件范围。

如果本阶段遇到 child `response.complete`，它应保留 `spawn_id`，并独立持久化
turn usage；聚合时必须按 `(task_id, spawn_id, turn_index)` 区分。

## 错误与异常场景

### invalid_finish

`run_result` 应持久化：

- `status="failed"`
- `reason="invalid_finish"`
- `usage`
- `usage_vendor_by_turn`
- `finish_detail.last_turn_usage`

这使输出长度截断、空响应、reasoning-only 等失败仍有可审计 usage。

### cancelled

取消路径可能发生在当前 LLM turn 完成之前。若没有 accepted response，则不产生
response.complete usage。`run_result` 使用当前已累计的 usage。

### retry

被 retry 丢弃的 incomplete attempt 不产生 response.complete。当前 `run_result.usage`
也不包含这些 attempt。这是本阶段接受的非账单级限制，需要后续 token audit 事件
补齐。

### compaction

context compaction summary LLM 的 usage 不并入 response.complete 或 run_result.usage。
本阶段只保留已有 `CompactionEvent.trigger_tokens` 估算字段。

## 测试计划

### 单元测试

- `ResponseEvent` 新字段可序列化和反序列化。
- `_public_content_for_event("response")`：
  - 无 usage 时返回字符串。
  - 有 usage 时返回结构化对象。
- `_public_content_for_event("run_result")`：
  - 包含 `num_turns`、`usage`、`usage_vendor_by_turn`。
  - 保留 `finish_detail`。
  - legacy `finish` 使用相同 shape。

### kernel 测试

- accepted natural response 产生 `response.complete`，并携带 `turn_usage` 和
  `total_usage`。
- 多 turn tool loop 中每个 accepted response.complete 的 `turn_index` 正确。
- retry 场景中，被丢弃 attempt 不产生 response.complete usage。
- invalid_finish 中 run_result 仍携带 usage 和 finish_detail。

### 持久化与回放测试

- `PersistenceHandler` 持久化结构化 response content。
- replay 解包结构化 response content 到字符串 `content`，并保留 usage metadata。
- `_dedupe_replayed_terminal_events()` 保持原有行为，response 不因结构化 content
  失去 dedupe 作用。
- `ChatHistoryConverter` 能从结构化 response content 恢复 assistant message。

### 兼容测试

- 旧的字符串 response history 仍可回放。
- 旧的 run_result content 不含 usage 时仍可被前端和 history converter 消费。

## 迁移与兼容

这是向后兼容的事件扩展：

- 新字段都是可选字段。
- response content 只有在 complete 且有 usage 时才结构化。
- replay 层会把结构化 response content 解包为字符串，保护前端旧渲染路径。
- run_result public content 只新增字段，不删除或重命名现有字段。

无需数据库 schema 迁移，因为 `evo_chat_events.content` 已是 JSON 字符串。

## 验收标准

- 新 run 的 DB response complete 行可查询到 `turn_usage` 和 `total_usage`。
- 新 run 的 DB run_result 行可查询到 `usage` 和 `num_turns`。
- 历史回放中 response 仍以字符串文本渲染，usage metadata 不破坏 UI。
- `ChatHistoryConverter` 不把 usage metadata 注入 LLM history。
- retry incomplete attempt 不生成持久化 complete response usage。
- 现有 replay dedupe 行为不回退。
