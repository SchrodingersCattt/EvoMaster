# Token Usage Event Carrier Design

## 背景

当前 agent 运行过程中，provider 返回的 token usage 已经在内核层被正确识别为
accepted LLM turn 的消耗：

1. `AgentKernel._run_items()` 收到 accepted `LLMResponse`。
2. `response.usage` 写入 `state.turn_usage`。
3. `response.usage` 累加进 `state.total_usage`。
4. `RunResultEvent.usage` 从 `state.total_usage` 生成最终汇总。

问题出在中间事件的 carrier 选择上：`ToolResultEvent` 当前也带
`turn_usage` / `total_usage`，而 `dispatch_tool_calls()` 会把父 LLM turn 的
usage 快照写到每个 `tool_result` 上。这个事件归属不准确，因为工具执行结果本身不
产生模型 token；真正产生 token 的是之前那次 LLM 调用，它可能输出了 reasoning、
可见文本和 tool calls。

本设计把 token usage 从 `tool_result` 迁移到模型输出侧事件：

- `thought.complete`
- `response.complete`
- `tool_call`

不修改旧 usage spec / plan 文档，不做历史数据兼容，不新增 ledger、表或事件类型。

## 目标

- 让 usage carrier 与真实 token 产生原因一致：LLM turn 产生 token，工具执行结果不
  产生 token。
- 保留 `RunResultEvent.usage` 作为最终 run-level 汇总真相。
- 保留 `state.total_usage` 作为内核运行期间的单一累计器。
- 让含 reasoning 的 accepted turn 可以在 `thought.complete` 上看到 usage。
- 让 tool-call turn 可以在 `tool_call` 上看到 usage。
- 让纯文本回答继续在 `response.complete` 上看到 usage。
- 删除 `ToolResultEvent` 上的 usage 字段和 public payload 投影。
- 不引入兼容分支。项目仍在开发阶段，旧字段迁移由外部脚本或手动处理，不在主代码中
  内联兼容逻辑。

## 非目标

- 不同步修改既有历史 spec / plan 文档。
- 不新增 `llm_turn_usage` 事件。
- 不改变 provider usage 抽取逻辑。
- 不改变 `RunResultEvent.usage` 的聚合语义。
- 不改变 subagent / compaction usage 如何进入 `state.total_usage`。
- 不改变 quota 或 billing 逻辑。
- 不从历史 DB 回扫、修复或迁移旧事件。

## 现状代码事实

### 事件模型

`ThoughtEvent` 当前只有文本、流状态、`token_count`、`context` 和
`reasoning_content`：

```python
class ThoughtEvent(EventBase):
    type: Literal["thought"] = "thought"
    content: str = ""
    stream_state: str | None = None
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None
    reasoning_content: str | None = None
```

`ResponseEvent` 已经有 usage 字段：

```python
turn_index: int | None = None
turn_usage: dict[str, int] = Field(default_factory=dict)
total_usage: dict[str, int] = Field(default_factory=dict)
usage_vendor: dict[str, Any] | None = None
```

`ToolCallEvent` 当前没有 usage 字段：

```python
class ToolCallEvent(EventBase):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
```

`ToolResultEvent` 当前有 usage 字段：

```python
turn_index: int | None = None
turn_usage: dict[str, int] = Field(default_factory=dict)
total_usage: dict[str, int] = Field(default_factory=dict)
```

### 内核生成链路

`AgentKernel._run_items()` 在 accepted `LLMResponse` 后执行：

```python
state.turn_usage = response.usage
accumulate_usage(state.total_usage, response.usage)
state.usage_vendor_by_turn.append(
    dict(response.usage_vendor) if response.usage_vendor else {}
)
```

随后：

- 可见文本会生成 usage-bearing `ResponseEvent(stream_state="complete")`。
- assistant state 会生成 usage-bearing `AssistantStateEvent`。
- tool calls 会生成不带 usage 的 `ToolCallEvent`。
- 工具执行完成后，`dispatch_tool_calls()` 生成 usage-bearing
  `ToolResultEvent`。

这导致工具结果事件看起来承担了模型 token 的来源。

### streaming thought 的 retry 边界

`agent_llm_stream.stream_llm_items()` 在流式阶段会发 `ThoughtEvent`：

- `stream_state="start"`
- `stream_state="streaming"`
- `stream_state="complete"`

这些事件发生在 `_call_llm_streaming()` 的 retry gate 之前。若直接在当前
`thought.complete` 上填 usage，retry 丢弃的 attempt 也可能落成 usage-bearing
审计事件。因此 accepted `thought.complete` 必须移到 `AgentKernel._run_items()` 中，
在 LLM response 通过 retry gate 后生成。

## 字段合同

### ThoughtEvent

新增字段：

```python
turn_index: int | None = None
turn_usage: dict[str, int] = Field(default_factory=dict)
total_usage: dict[str, int] = Field(default_factory=dict)
usage_vendor: dict[str, Any] | None = None
```

语义：

- `stream_state in {"start", "streaming", "segment_end", "end"}` 的 thought 事件是
  流式显示或 segment control，不带 usage。
- `stream_state == "complete"` 的 thought 事件是 accepted LLM turn 的 reasoning
  审计事件，可以带 usage。
- `turn_usage` 表示本次 accepted LLM turn 的 scalar usage。
- `total_usage` 表示截至该 accepted turn 后的 run-level scalar usage 快照。
- `usage_vendor` 表示本次 accepted LLM turn 的 provider-native usage 快照。

### ResponseEvent

保留现有 usage 字段和语义。

`response.complete` 仍表示 accepted LLM turn 中的可见文本回答。它可以带 usage。

如果一个 turn 同时有 reasoning、可见文本和 tool calls，`response.complete` 与
`thought.complete`、`tool_call` 可能指向同一个 `turn_index`。消费者不能简单累加多个
事件的 `turn_usage`，必须按 `turn_index` 去重，或直接以 `RunResultEvent.usage` 为汇
总真相。

### ToolCallEvent

新增字段：

```python
turn_index: int | None = None
turn_usage: dict[str, int] = Field(default_factory=dict)
total_usage: dict[str, int] = Field(default_factory=dict)
usage_vendor: dict[str, Any] | None = None
```

语义：

- `tool_call` 表示 accepted LLM turn 请求工具调用。
- `turn_usage` 是触发这些 tool calls 的那次 LLM 调用的 usage，不是工具执行的消耗。
- 同一 turn 多个 tool calls 默认都可携带同一份 usage 快照和相同 `turn_index`。
- 若消费者需要从事件流统计 usage，应按 `turn_index` 去重。

这个选择比只让第一个 `tool_call` 带 usage 更直观：每个 tool call 都能自解释其来自
哪次模型调用，以及该模型调用消耗了多少 token。重复计数风险通过 `turn_index` 合同
解决。

### ToolResultEvent

删除字段：

```python
turn_index: int | None = None
turn_usage: dict[str, int] = Field(default_factory=dict)
total_usage: dict[str, int] = Field(default_factory=dict)
```

修改后语义：

- `tool_result` 只表示工具执行结果。
- `tool_result.payload` 可以保留工具自身结构化信息，例如 `figures` 或
  `subagent_usage`。
- `Agent` 工具的 `payload["subagent_usage"]` 是子 agent 作为工具产生的 usage delta
  证据，但它不等同于 `ToolResultEvent.turn_usage`，也不应投影为 `tool_result` 顶层
  usage 字段。

### AssistantStateEvent

保持现状：内部持久化 assistant message state 时可以继续带
`turn_index` / `turn_usage` / `total_usage`。

这个事件不是前端展示的正式 usage carrier，但它对 provider state、tool calls 和诊断
排障有价值，因此不在本次迁移中删除。

### RunResultEvent

保持现状：`usage` 继续是整次 run 的最终累计 scalar usage。

汇总统计、飞书通知、devshell summary、评测 ingest 等需要最终总量的消费者优先使用
`RunResultEvent.usage`，而不是从中间事件重复累加。

## 事件生成设计

### 流式阶段

`stream_llm_items()` 继续负责实时输出：

- `ThoughtEvent(stream_state="start")`
- `ThoughtEvent(stream_state="streaming")`
- `ResponseEvent(stream_state="streaming")`
- `ResponseEvent(stream_state="segment_end")`
- `ResponseEvent(stream_state="end")`

修改点：

- 现有 pre-gate `ThoughtEvent(stream_state="complete")` 改成
  `ThoughtEvent(stream_state="segment_end")`。
- `segment_end` 只表示 reasoning segment 封口，不携带 usage，不持久化，不作为
  accepted audit event。

### Accepted LLM response 阶段

`AgentKernel._run_items()` 收到 accepted `LLMResponse` 后：

1. 写入 `state.turn_usage`。
2. 累加 `state.total_usage`。
3. 记录 `state.usage_vendor_by_turn`。
4. 计算 `turn_index = state.turn - 1`。
5. 生成 usage 快照：

```python
turn_usage_snapshot = dict(state.turn_usage)
total_usage_snapshot = dict(state.total_usage)
usage_vendor_snapshot = response.usage_vendor or None
```

如果 `response.reasoning_content` 非空，发 accepted thought：

```python
ThoughtEvent(
    source="agent",
    content=response.reasoning_content,
    stream_state="complete",
    reasoning_content=response.reasoning_content,
    turn_index=turn_index,
    turn_usage=turn_usage_snapshot,
    total_usage=total_usage_snapshot,
    usage_vendor=usage_vendor_snapshot,
)
```

如果 root run 有非 trivial 可见文本，继续发 accepted response：

```python
ResponseEvent(
    source="agent",
    content=response.content,
    stream_state="complete",
    turn_index=turn_index,
    turn_usage=turn_usage_snapshot,
    total_usage=total_usage_snapshot,
    usage_vendor=usage_vendor_snapshot,
    model=state.llm_model,
    model_profile=state.llm_model_profile,
    model_route=state.llm_model_route,
)
```

如果有 tool calls，每个 tool call 发：

```python
ToolCallEvent(
    source="agent",
    call_id=tc.id,
    tool_name=tc.name,
    arguments=tc.arguments,
    turn_index=turn_index,
    turn_usage=turn_usage_snapshot,
    total_usage=total_usage_snapshot,
    usage_vendor=usage_vendor_snapshot,
)
```

`ToolResultEvent` 改为：

```python
ToolResultEvent(
    source="agent",
    call_id=tc.id,
    tool_name=tc.name,
    result=tool_result.content,
    status=tool_result.status,
    payload=tool_result.payload,
)
```

### 多 tool call

同一 LLM turn 产生多个 tool calls 时：

- 多个 `tool_call` 使用同一个 `turn_index`。
- 多个 `tool_call.turn_usage` 内容相同。
- 不要求每个 `tool_call` 的 usage 是独立 delta。
- 中间事件消费者必须按 `turn_index` 去重。
- 最终 run 汇总仍以 `RunResultEvent.usage` 为准。

### retry

只有通过 `_call_llm_streaming()` retry gate 的 `LLMResponse` 才能产生 usage-bearing：

- `thought.complete`
- `response.complete`
- `tool_call`

retry 中被丢弃的 incomplete attempt 只可能产生 streaming / segment events，不会产生
usage-bearing audit events。

### invalid finish

如果 accepted response 进入 invalid finish：

- `state.total_usage` 仍应包含该 accepted response 的 usage。
- `FinishDetail.last_turn_usage` 继续记录最后一轮 root LLM usage。
- 如果该 response 有 `reasoning_content`，可以发 usage-bearing `thought.complete`，
  作为 reasoning-only 或异常完成的审计证据。
- 如果没有可见文本且没有 tool calls，不发 `response.complete` 和 `tool_call`。
- 最终 `RunResultEvent(status="failed", reason="invalid_finish")` 携带累计 usage。

## Public Payload Mapping

### shared usage keys

把 event payload 映射里的 response-only usage key 概念收敛为共享 usage keys：

```python
_USAGE_KEYS = (
    "turn_index",
    "stream_id",
    "turn_usage",
    "total_usage",
    "usage_vendor",
)
```

`response` 和 `thought` 都可以用结构化 content 持久化，再在 live/replay SSE 出口解包
回字符串 content。

### thought

无 usage 时保持字符串 content：

```json
"reasoning delta"
```

有 usage 时持久化为结构化 content：

```json
{
  "content": "reasoning text",
  "turn_index": 0,
  "stream_id": "turn-3",
  "turn_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  },
  "total_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  },
  "usage_vendor": {
    "inputTokens": 100,
    "outputTokens": 20
  }
}
```

SSE 输出时解包为：

```json
{
  "type": "thought",
  "content": "reasoning text",
  "turn_index": 0,
  "turn_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  },
  "total_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  }
}
```

### tool_call

`tool_call` public content 增加 usage 字段：

```json
{
  "id": "call_1",
  "call_id": "call_1",
  "name": "Bash",
  "args": {
    "cmd": "ls"
  },
  "turn_index": 0,
  "turn_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  },
  "total_usage": {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120
  },
  "usage_vendor": {
    "inputTokens": 100,
    "outputTokens": 20
  }
}
```

### tool_result

`tool_result` public content 删除 usage 投影，只保留工具执行结果：

```json
{
  "id": "call_1",
  "call_id": "call_1",
  "name": "Bash",
  "result": "output",
  "status": "success",
  "info": {}
}
```

`payload["subagent_usage"]` 若存在，仍位于 `info.subagent_usage`，作为工具 payload 的一
部分保留。

## 持久化与回放

`PersistenceHandler` 继续跳过：

- `stream_state="start"`
- `stream_state="streaming"`
- `stream_state="segment_end"`
- `stream_state="end"`

需要确保 `ThoughtEvent(stream_state="segment_end")` 也被跳过。当前 handler 已按
`ThoughtEvent | ResponseEvent` 判断 streaming states，因此新增 thought `segment_end`
不需要额外分支。

`SSEHandler` 继续跳过：

- `ThoughtEvent(stream_state="complete")`
- `ResponseEvent(stream_state="complete")`
- `stream_state="segment_end"`

因此 usage-bearing `thought.complete` 和 `response.complete` 主要用于持久化和 replay，
不会在 live streaming 中重复打扰前端。

`stream_sse_filter.normalize_response_sse_payload()` 应扩展为同时处理 `thought` 的结构
化 content，使 replay SSE 仍保持 `content` 为字符串。

`ChatHistoryConverter._assistant_content()` 已能从 `{content: ...}` 结构中提取文本，
因此 thought / response 结构化 content 不应破坏历史恢复文本。

## 统计规则

中间事件上的 `turn_usage` 是快照，不是 delta ledger。

允许多个事件拥有相同 `turn_index` 和相同 `turn_usage`：

- `thought.complete`
- `response.complete`
- 一个或多个 `tool_call`
- `assistant_state`

因此统计规则是：

- 最终总量：使用 `RunResultEvent.usage`。
- 单轮审计：按 `turn_index` 查看模型输出侧 carrier。
- 从事件流近似重建 root LLM usage：按 `turn_index` 去重后聚合
  `turn_usage`。
- 不从 `tool_result` 聚合 token usage。
- 不从 `usage_vendor_by_turn` 反推混合总量；它仍只代表 root accepted turns 的
  provider-native 快照。

## 实现任务建议

### Task 1: 事件模型迁移

修改 `matmaster/types/events.py`：

- `ThoughtEvent` 新增 usage 字段。
- `ToolCallEvent` 新增 usage 字段。
- `ToolResultEvent` 删除 usage 字段。

更新 `tests/matmaster/types/test_events.py`：

- 增加 thought usage 字段测试。
- 增加 tool_call usage 字段测试。
- 删除或反转 tool_result usage 字段测试。

### Task 2: accepted thought 与 tool_call carrier

修改 `matmaster/core/agent_llm_stream.py`：

- pre-gate `ThoughtEvent(stream_state="complete")` 改为
  `stream_state="segment_end"`。

修改 `matmaster/core/agent.py`：

- accepted `LLMResponse` 后，如果 `response.reasoning_content` 非空，生成
  usage-bearing `ThoughtEvent(stream_state="complete")`。
- `ToolCallEvent` 构造时加入 `turn_index`、`turn_usage`、`total_usage`、
  `usage_vendor`。

更新 `tests/matmaster/core/test_agent_kernel_usage_events.py`：

- reasoning-only / reasoning-then-tool-call 场景断言 `thought.complete` 带 usage。
- tool-call turn 断言 `tool_call` 带 usage。
- retry 场景断言丢弃 attempt 不产生 usage-bearing complete thought。

### Task 3: tool_result 去 usage

修改 `matmaster/core/agent_tool_dispatch.py`：

- `ToolResultEvent` 构造不再传 `turn_index`、`turn_usage`、`total_usage`。
- 保留 `extract_tool_usage_delta()` 对 `Agent` 工具 payload 的累计逻辑。
- 保留 `state.total_usage` 累计，因为 `RunResultEvent.usage` 仍要包含 subagent usage。

更新 `tests/matmaster/core/test_agent_tool_dispatch.py`：

- 不再断言 tool_result usage 字段。
- 继续断言 `state.total_usage` 被 subagent usage 更新。

### Task 4: public payload 映射

修改 `matmaster/integration/event_payloads.py`：

- 抽出共享 usage keys。
- `_public_content_for_event("thought", ...)` 支持 usage-bearing 结构化 content。
- `_public_content_for_event("tool_call", ...)` 投影 usage 字段。
- `_public_content_for_event("tool_result", ...)` 删除 usage 投影。
- response/thought SSE normalization 共用结构化 content 解包逻辑。

更新 `tests/matmaster/integration/test_event_payloads.py`：

- thought usage-bearing content 映射测试。
- tool_call usage-bearing content 映射测试。
- tool_result 不投影 usage 测试。
- replay normalization 对 thought / response 都保持字符串 content。

## 验证命令

最小验证：

```bash
uv run pytest tests/matmaster/types/test_events.py -q
uv run pytest tests/matmaster/core/test_agent_kernel_usage_events.py -q
uv run pytest tests/matmaster/core/test_agent_tool_dispatch.py -q
uv run pytest tests/matmaster/integration/test_event_payloads.py -q
uv run pytest tests/matmaster/integration/test_sse_handler_mode_filter.py -q
```

changed-files pre-commit：

```bash
uv run --extra dev pre-commit run --files \
  matmaster/types/events.py \
  matmaster/core/agent.py \
  matmaster/core/agent_llm_stream.py \
  matmaster/core/agent_tool_dispatch.py \
  matmaster/integration/event_payloads.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py \
  tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/integration/test_event_payloads.py
```

## 风险与边界

### 重复计数风险

多个事件携带同一 `turn_usage`，因此错误消费者可能重复累加。设计通过明确
`turn_index` 去重规则解决这个问题。正式汇总消费者应优先使用 `RunResultEvent.usage`。

### 历史数据字段漂移

旧 DB 事件可能仍有 `tool_result.content.turn_usage`。本设计不在主代码中兼容旧 shape。
如需要清理历史数据，应走外部迁移脚本。

### UI 展示变化

live SSE 当前跳过 complete thought / response，所以前端实时显示基本不受影响。
历史 replay 如果展示 thought / tool_call metadata，可能会看到 usage 从 tool_result
移动到 thought / tool_call，这是预期变化。

### subagent usage

`Agent` 工具的 child usage 仍通过 `payload["subagent_usage"]` 进入父 run
`state.total_usage`。它不是 `tool_result` 顶层 usage，也不代表工具结果本身产生
token。这个 payload 是子 agent run 的结果证据。

## 最终合同

- LLM token usage 属于 accepted LLM turn。
- `thought.complete` 承载 reasoning 侧 accepted turn usage。
- `response.complete` 承载可见文本侧 accepted turn usage。
- `tool_call` 承载工具调用侧 accepted turn usage。
- `tool_result` 不承载 token usage。
- `assistant_state` 可保留内部 usage 快照。
- `run_result.usage` 是最终汇总真相。
- 中间事件上的 usage 是快照，不是可直接求和的 delta。
