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
- `_stream_llm_items()` 当前在 stream 内部也会发 `response.complete`：一次用于
  content 到 tool-call 的 segment 封口，一次用于 stream `finally` 收尾。这两类
  complete 都发生在 retry gate 之前，语义上不是 accepted turn audit event。

本设计补齐两个互补层次：

1. 在完整的 `response` 事件中持久化当前已接受 LLM turn 的 usage。
2. 在 `run_result` public content 中持久化整次 run 的终态 usage。

## 目标

- 让历史 DB 中能查询到每个完整 answer segment 对应的单轮 usage。
- 让历史 DB 中能查询到每次 run 的终态累计 usage。
- 保持 live SSE、历史回放、`ChatHistoryConverter` 的文本兼容性。
- 避免 retry 中被丢弃的 incomplete attempt 被误记为 accepted response usage。
- 明确 response 级 usage 与 run_result 级 usage 的不同语义。
- 明确 live SSE、DB persistence、replay SSE 三条路径下 `content` 与 usage 的形态。

## 非目标

- 不实现账单级全量成本统计。`run_result.usage` 不等于真实账单成本；retry 失败
  attempt、context compaction summary LLM、subagent 内部 LLM 消耗等可在后续专门的
  token audit 事件中补充。
- 不改变 quota 扣减语义。当前 `use_quota` 仍是成功后扣一次额度，不按 token 扣费。
- 不要求 streaming response chunk 实时携带 usage。provider usage 通常要到 LLM
  turn 结束才可用。
- 不重构 replay dedupe 的整体策略，只做 response content 结构化后的兼容处理。
- 不在本阶段实现 sub-agent 全量 usage 聚合；本阶段 root run audit 优先。

## 当前链路

Provider 层：

- `OpenAIProvider.chat_stream()` 通过 `stream_options.include_usage=True` 取得
  usage-only chunk。
- `BedrockProvider.chat_stream()` 从 `metadata.usage` 取得 usage。
- Provider 统一输出 `StreamChunk.usage` / `LLMResponse.usage`。

Kernel 层：

- `_stream_llm_items()` 聚合流式文本、reasoning、tool call delta 和最终 usage。
- `_stream_llm_items()` 当前会在 retry gate 之前发 `response.complete` segment marker；
  实现时必须把这类 marker 与 accepted `response.complete` 区分开。
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
- `build_public_sse_payload_from_bus_dump()` 也使用 `_public_content_for_event()` 组装
  live SSE payload；虽然当前 live `SSEHandler` 会跳过 `stream_state="complete"` 的
  response，结构化 content 仍必须有统一的 SSE 解包规则，避免未来或 replay 路径把
  dict 作为 `content` 直接发给前端。

## 设计原则

### response 表达单轮 accepted usage

完整的 `response` 事件表示某次 LLM turn 已形成可见 assistant response。它携带的
usage 只表示这一轮 LLM call，不代表整次 run 的最终成本。

### run_result 表达终态 run usage

`run_result` 是业务终态。它携带的 usage 是 root kernel 已累计的 accepted LLM
turn usage 总和，适合做 run 级查询、展示和粗粒度审计。

### run_result usage 不是账单成本

本阶段的 usage 只统计 root kernel 中通过 retry gate 后进入 `state.total_usage` 的
LLM response。被 retry 丢弃的 attempt、compaction summary LLM、sub-agent 内部 LLM
调用不在该数字内。因此它适合做产品内审计和排查，不适合作为账单结算依据。

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
| `usage_vendor` | 当前 turn 的 provider-native usage 快照，按 provider 原样保留，不在 kernel 层 normalize |

`ResponseEvent.stream_state` 语义调整为：

- `start` / `streaming` / `end`：live 文本流控制。
- `segment_end`：stream 内部 content segment 封口，发生在 retry gate 之前，不带 usage，
  不持久化，不作为审计事件。
- `complete`：retry gate 之后的 accepted turn audit event，可带 usage，可持久化。

为让 usage 兜底来源可以被稳定去重，已有的 usage-bearing events 也应补可选
`turn_index`：

- `AssistantStateEvent.turn_index: int | None`
- `ToolResultEvent.turn_index: int | None`

这两个字段只用于审计 key，不改变它们现有 content 语义。

`RunResultEvent` 模型本身无需新增字段。它已经包含：

- `num_turns`
- `usage`
- `usage_vendor_by_turn`
- `finish_detail`

本阶段只扩展 public content mapping。

## 事件生成设计

### streaming response

`stream_state in {"start", "streaming", "segment_end", "end"}` 的 response 继续走当前
stream path，只用于 live 文本流或内部 segment control，不携带 usage，也不持久化。

当前 `_stream_llm_items()` 中两类 retry-gate-before 的 `response.complete` 必须改成
`response.segment_end`：

- content 到 tool-call delta 的 segment 切换 marker。
- stream `finally` 中对 in-progress content segment 的封口 marker。

`segment_end` 是对旧内部 marker 的重命名。当前 `SSEHandler` 已跳过
`stream_state="complete"` 的 response；实现时应让它也跳过 `segment_end`。持久化层应把
`segment_end` 加入 streaming/ephemeral 状态集合，避免 retry 前的 segment marker 写入 DB。

### complete response

`stream_state="complete"` 的 response 应在 accepted `LLMResponse` 被确认后生成，
而不是在 `_stream_llm_items()` 的 segment 切换或 `finally` 中生成。

推荐流程：

1. `_stream_llm_items()` 仍负责 streaming 文本事件和最终 `LLMResponse`。
2. `_stream_llm_items()` 对 content segment 封口只发 `segment_end`，不发 audit
   `complete`。
3. `_call_llm_streaming()` 继续负责 retry 判断。
4. `_run_items()` 收到 accepted `LLMResponse` 后：
   - 提取 `turn_usage`
   - 累加 `state.total_usage`
   - append `usage_vendor_by_turn`
   - 在 root kernel 且文本非 trivial 时生成 `response.complete`，携带 `turn_index`、
     当前 turn usage 和累计 usage
   - 继续生成 `AssistantStateEvent` / `ToolCallEvent` / `ToolResultEvent` 或 terminal

这样可以避免 first attempt incomplete response 被 retry 后仍留下完整 response
usage 记录。

### complete response 的发出条件

只在以下条件满足时发出持久化 complete response：

- `response.content` 有有效可见文本。
- 文本不是 trivial response，如只有省略号或空白。
- response 已通过 `_call_llm_streaming()` 的 retry gate，属于 accepted turn。
- 本阶段仅对 root kernel 生成 usage-bearing persisted `response.complete`。

对于 tool-call turn 中模型先输出少量可见文本再发 tool call 的情况，仍可以发
`response.complete`，但该 turn 后续还会发 `assistant_state` 和 tool events。
如果前置文本是 trivial response，则该 turn 不持久化 `response.complete`，由
`assistant_state.turn_usage` 作为 turn-level 审计兜底。审计消费者必须按
`turn_index` 去重，不从多个事件简单求和。

## Public Content Mapping

### 事件路径矩阵

同一个内部 event 在三条路径下的 `content` 形态不同：

| 路径 | `content` 形态 | usage 位置 |
|---|---|---|
| Live SSE | 扁平字符串 | event 顶层 `turn_index` / `turn_usage` / `total_usage` / `usage_vendor`；当前 complete response 仍会被 `SSEHandler` 跳过 |
| Persist DB | 结构化 dict | 嵌在 `content` JSON 中，便于 DB 行自包含 |
| Replay SSE | 扁平字符串 | 从 DB structured content 解包到 event 顶层 |
| ChatHistoryConverter | 扁平 assistant text | 只读取 `content.content`，丢弃 usage metadata |

实现上不要让结构化 response content 直接流到前端。推荐保留一个统一的
response-content unpack helper，并在两个出口使用：

- `build_public_sse_payload_from_bus_dump()`：对 live SSE payload 做最终 normalization。
- `_normalize_replayed_event()`：对 DB replay event 做同样 normalization。

`PersistenceHandler` 写 DB 时使用结构化 content。live/replay SSE 则必须把
`content.content` 提到顶层 `content`，并把 `turn_index`、`turn_usage`、`total_usage`、
`usage_vendor` 提到 event 顶层。

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

如果 `_public_content_for_event()` 同时被 persistence 和 SSE 调用，则 SSE builder
必须在返回给前端前解包上述结构，不能把 dict 放进 SSE 的 `content` 字段。

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

## 审计不变量与 usage 权威性

同一 root run 内，turn-level usage 的稳定 key 是
`(task_id, spawn_id, turn_index)`。本阶段实际聚合以 `spawn_id is None` 的 root
事件为主。

同一 key 下，`turn_usage` 的权威来源按以下优先级回退：

1. `response.complete.turn_usage`：纯文本 turn 和带非 trivial 前置文本的 tool-call
   turn 的首选来源。
2. `assistant_state.turn_usage`：tool-call turn 的兜底来源，尤其覆盖 trivial 前置文本
   被 `PersistenceHandler` 跳过的情况。
3. `tool_result.turn_usage`：最后兜底。它会在同一 turn 的多个 tool result 上重复，
   只能在按 `turn_index` 去重后使用，不能按 event 行简单求和。

必须满足的实现不变量：

- 每个 root accepted turn 最多持久化 1 条 usage-bearing `response.complete`。
- 每个 root tool-call accepted turn 至少持久化 1 条带 `turn_usage` 的
  `assistant_state`，即使 visible preamble 是 `...` 这类 trivial response。
- 对 completed root run，`run_result.usage` 应等于按 distinct `turn_index` 汇总后的
  turn usage 之和。比较时只比较 normalized scalar usage key，例如
  `prompt_tokens`、`completion_tokens`、`total_tokens`。
- invalid_finish/cancelled/retry 等失败边界不要求满足 completed-run 等式；
  `run_result.usage` 和 `finish_detail.last_turn_usage` 是失败场景的权威审计来源。
- `usage_vendor` / `usage_vendor_by_turn` 只保存 provider-native 快照，不做跨 provider
  normalize；若未来要统一字段，应在 view/reporting 层单独实现。

## Replay 兼容

DB 中结构化 response content 回放时需要解包：

输入 DB event：

```json
{
  "type": "response",
  "content": {
    "content": "answer text",
    "turn_index": 3,
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
  "turn_index": 3,
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
- `segment_end` 不持久化，不参与 response-seen dedupe。legacy 历史中无 usage 的
  `response.complete` 仍按旧 replay 行为处理。

后续若需要前端在 replay 中一定看到 run_result usage，可以新增轻量的
`token_usage_snapshot` 事件，而不是破坏 run_result dedupe。

## 子 agent 语义

本设计保持当前 root run 语义：

- root `run_result.usage` 只表示 root kernel accepted LLM turns 的累计 usage。
- 本阶段只在 root kernel（`spawn_id is None`）处理 usage-bearing
  `response.complete` 持久化。
- subagent 事件通过 `spawn_id` 转发，但 child terminal event 当前不稳定进入 public
  persistence path。
- 子 agent 全量成本审计属于后续 token audit 事件范围。

如果 child kernel 在旧路径中产生 response event，本阶段不新增 child usage 聚合分支。
实现和测试应明确证明 child response 不会被 root run audit 误聚合。

## 错误与异常场景

### invalid_finish

`run_result` 应持久化：

- `status="failed"`
- `reason="invalid_finish"`
- `usage`
- `usage_vendor_by_turn`
- `finish_detail.last_turn_usage`

这使输出长度截断、空响应、reasoning-only 等失败仍有可审计 usage。

失败场景下不要求一定存在 `response.complete.turn_usage`。如果有可见文本并选择持久化
response，它必须遵守同样的结构化 content 和 replay 解包规则；但审计以
`run_result.usage` / `finish_detail.last_turn_usage` 为准。

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
- `AssistantStateEvent` / `ToolResultEvent` 的 `turn_index` 可序列化和反序列化。
- `_public_content_for_event("response")`：
  - 无 usage 时返回字符串。
  - 有 usage 时返回结构化对象。
- SSE/replay response unpack helper：
  - 输入结构化 response content 时输出字符串 `content`。
  - `turn_index`、`turn_usage`、`total_usage`、`usage_vendor` 被提升到 event 顶层。
- `_public_content_for_event("run_result")`：
  - 包含 `num_turns`、`usage`、`usage_vendor_by_turn`。
  - 保留 `finish_detail`。
  - legacy `finish` 使用相同 shape。

### kernel 测试

- accepted natural response 产生 `response.complete`，并携带 `turn_usage` 和
  `total_usage`。
- 多 turn tool loop 中每个 accepted response.complete 的 `turn_index` 正确。
- `_stream_llm_items()` 在 content 到 tool-call 切换和 stream `finally` 时只产生
  `segment_end`，不产生 usage-bearing `complete`。
- retry 场景中，被丢弃 attempt 不产生 response.complete usage。
- invalid_finish 中 run_result 仍携带 usage 和 finish_detail。
- child/sub-agent response 不会进入 root run 的 turn-level usage 聚合。

### 持久化与回放测试

- `PersistenceHandler` 持久化结构化 response content。
- `PersistenceHandler` 不持久化 `response.segment_end`。
- replay 解包结构化 response content 到字符串 `content`，并保留 `turn_index` 与 usage
  metadata。
- `_dedupe_replayed_terminal_events()` 保持原有行为，response 不因结构化 content
  失去 dedupe 作用。
- `ChatHistoryConverter` 能从结构化 response content 恢复 assistant message。
- trivial response.complete 不持久化时，同一 tool-call turn 的 `assistant_state` 仍提供
  `turn_usage` / `turn_index` 兜底。

### 兼容测试

- 旧的字符串 response history 仍可回放。
- 旧的 run_result content 不含 usage 时仍可被前端和 history converter 消费。
- legacy 无 usage 的 `response.complete` history 仍可回放，不参与新审计聚合。

## 迁移与兼容

这是向后兼容的事件扩展：

- 新字段都是可选字段。
- response content 只有在 complete 且有 usage 时才结构化。
- replay 层会把结构化 response content 解包为字符串，保护前端旧渲染路径。
- run_result public content 只新增字段，不删除或重命名现有字段。
- `segment_end` 是新增 stream_state 值；旧 history 中的 `complete` 字符串仍兼容，
  新实现只把 retry 后的 accepted audit event 命名为 `complete`。

无需数据库 schema 迁移，因为 `evo_chat_events.content` 已是 JSON 字符串。

## 验收标准

- 新 run 的 DB response complete 行可查询到 `turn_usage` 和 `total_usage`。
- 新 run 的 DB run_result 行可查询到 `usage` 和 `num_turns`。
- 历史回放中 response 仍以字符串文本渲染，usage metadata 不破坏 UI。
- `ChatHistoryConverter` 不把 usage metadata 注入 LLM history。
- retry incomplete attempt 不生成持久化 complete response usage。
- retry gate 之前的 response segment marker 使用 `segment_end`，不写入 DB。
- tool-call turn 的 trivial preamble 不丢失 usage 审计，因为 `assistant_state` 带
  `turn_index` / `turn_usage`。
- completed root run 的 `run_result.usage` 能与 distinct turn-level usage 对账。
- 现有 replay dedupe 行为不回退。
