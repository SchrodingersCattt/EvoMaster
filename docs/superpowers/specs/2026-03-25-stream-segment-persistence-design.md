# Stream Segment Persistence Design

## Problem

`_call_llm` 中的流式事件目前只被 SSEHandler 逐 chunk 推送给前端，PersistenceHandler 跳过所有流状态事件（stream_state in start/streaming/end）。当一个逻辑段落（thinking 或 response）完整产出后，没有对应的持久化事件写入数据库。

这导致：
- 用户断线重连后无法从 DB 重放到最新的完整段落
- 缺少段落级别的审计追踪记录

## Goals

- 在每个逻辑段落（thinking、response）完整产出后，向数据库写入一条包含完整累积内容的持久化事件
- 支持断线恢复和审计追踪两个用途
- 纯增量改动，不改变现有 SSE 逐 chunk 推送行为

## Non-Goals

- 不引入新事件类型（复用 ThoughtEvent / ResponseEvent）
- 不引入 tool_calls 段落的持久化（已有 ToolCallEvent / ToolResultEvent 覆盖）
- 不改动 PersistenceHandler 的过滤逻辑
- 不改动 stream_service.py 的回放逻辑
- 不处理跨 turn 的段落合并（每次 `_call_llm` 调用独立产生段落事件）

## Key Design Decision: 复用已有事件类型 + stream_state="complete"

不引入新的 `SegmentCompleteEvent`，而是复用 `ThoughtEvent` / `ResponseEvent`，用 `stream_state="complete"` 标记段落完成。理由：

1. **PersistenceHandler** 的 `_STREAMING_STATES = {"start", "streaming", "end"}` 不含 `"complete"` → 自动持久化，无需改动
2. **stream_service.py** 回放路径已知如何处理 thought/response → 自动支持断线恢复，无需改动
3. **SSEHandler** 仅需新增一行：跳过 `stream_state="complete"` 的事件（live 推送时前端已从流式 chunk 获得内容）

对比引入新事件类型 `SegmentCompleteEvent` 的方案，该方案避免了：
- stream_service.py 回放时需要转换新类型为前端已知类型
- event_payloads.py 需要新增内容映射
- 前端需要识别新的事件类型

## Design

### 1. 段落边界检测（`matmaster/core/agent.py` — `_call_llm`）

在流循环中增加两个状态标志追踪当前正在产出的内容类型：

```python
producing_reasoning = False
producing_content = False
```

检测三个转换点：

| 转换 | 触发条件 | 动作 |
|------|----------|------|
| reasoning → content | `producing_reasoning=True` 且首个 `chunk.content` 到来 | 发射 `on_segment_complete("thought", 累积reasoning, stream_id)`，`producing_reasoning=False` |
| content → tool_calls | `producing_content=True` 且首个 `chunk.tool_call_deltas` 到来 | 发射 `on_segment_complete("response", 累积content, stream_id)`，`producing_content=False` |
| stream 结束（finally） | `producing_reasoning=True` 或 `producing_content=True` | 发射对应段落的 `on_segment_complete` |

累积文本复用已有的 `content_parts` / `reasoning_parts` 局部变量，通过 `"".join()` 获取当前值。

**实现约束**：某些 LLM provider 可能在同一个 chunk 中同时返回 `reasoning_content` 和 `content`。实现时必须先累积 `reasoning_content`，再做转换检测，确保最后一个 reasoning token 不丢失。处理顺序：

1. 累积 `chunk.reasoning_content` 到 `reasoning_parts`（如有）
2. 检测 reasoning → content 转换并发射段落完成
3. 累积 `chunk.content` 到 `content_parts`（如有）
4. 检测 content → tool_calls 转换并发射段落完成

### 2. 新 Hook 方法（`matmaster/core/hooks.py`）

**Hook Protocol** 新增第 7 个方法：

```python
def on_segment_complete(self, segment_type: str, content: str, stream_id: str | None) -> None: ...
```

- `segment_type`: `"thought"` | `"response"`
- `content`: 该段落的完整累积文本
- `stream_id`: 关联到对应的流（格式 `"turn-{N}"`）

**BaseHook** 默认 no-op 实现。

**Runner 函数** `run_on_segment_complete`：观察型，遍历所有 hooks，使用 `getattr` 保持向后兼容（同 `on_guard_blocked` 模式）。

```python
def run_on_segment_complete(
    hooks: list[Hook], segment_type: str, content: str, stream_id: str | None
) -> None:
    for hook in hooks:
        fn = getattr(hook, "on_segment_complete", None)
        if fn is not None:
            fn(segment_type, content, stream_id)
```

hooks.py 的模块文档字符串和 Hook Protocol 的 docstring 需同步更新方法计数（Six → Seven）。

### 3. EventEmitterHook 实现（`matmaster/core/hooks.py`）

复用已有事件类型，用 `stream_state="complete"` 标记：

```python
def on_segment_complete(self, segment_type: str, content: str, stream_id: str | None) -> None:
    if segment_type == "thought":
        self._bus.emit(
            ThoughtEvent(
                source=self._source,
                content=content,
                stream_state="complete",
                stream_id=stream_id,
                reasoning_content=content,
            )
        )
    elif segment_type == "response":
        self._bus.emit(
            ResponseEvent(
                source=self._source,
                content=content,
                stream_state="complete",
                stream_id=stream_id,
            )
        )
```

### 4. SSEHandler 过滤更新（`matmaster/integration/sse_handler.py`）

在 `_should_skip` 方法顶部新增，跳过 `stream_state="complete"` 的 ThoughtEvent/ResponseEvent：

```python
if isinstance(event, (ThoughtEvent, ResponseEvent)) and event.stream_state == "complete":
    return True
```

使用 `isinstance` 检查而非 `getattr`，与 PersistenceHandler 第 52 行的模式保持一致。

**PersistenceHandler 无需改动**：`"complete"` 不在 `_STREAMING_STATES = {"start", "streaming", "end"}` 中，自动持久化。

### 5. stream_state 注释更新（`matmaster/types/events.py`）

ThoughtEvent 和 ResponseEvent 的 `stream_state` 字段注释更新，加入 `"complete"`：

```python
stream_state: str | None = None  # 'start' | 'streaming' | 'end' | 'complete' | None
```

无功能改动，仅注释。

### 6. AssistantMessage 类型扩展（`evomaster/utils/types.py`）

`chat_history.py` 导入的是 `evomaster.utils.types.AssistantMessage`，该类当前只有 `content` 和 `tool_calls` 字段，没有 `reasoning_content`。需要添加：

```python
class AssistantMessage(BaseMessage):
    role: MessageRole = MessageRole.ASSISTANT
    tool_calls: list[ToolCall] | None = Field(default=None, description='工具调用列表')
    reasoning_content: str | None = None
```

不添加此字段会导致下方 chat_history.py 合并逻辑中 `reasoning_content=pending_reasoning` 参数被 Pydantic 静默忽略，reasoning 内容丢失。

### 7. chat_history.py thought 缓存合并（`src/services/chat_history.py`）

**问题**：引入 thought(complete) 持久化后，同一 turn 中 DB 会同时存在 thought 和 response 事件。当前 chat_history.py 为每个 thought 和 response 各创建一个 AssistantMessage，导致：
- reasoning 文本被放入 AssistantMessage.content（语义错误，应放入 reasoning_content）
- 多轮对话中出现连续 assistant 消息

**方案**：将 thought 的即时创建改为缓存，遇到 response 或 run_result 时合并。

**改动**：

新增 `pending_reasoning` 变量（在 turn 级变量初始化处）：

```python
pending_reasoning: str | None = None
```

**thought 处理**（原 276-284 行）— 缓存而非立即创建：

```python
if _is_matmaster_source(source) and typ in ('thought', 'planner_reply'):
    flush_tool_calls()
    assistant_state_tool_ids.clear()
    last_assistant_text_idx = None
    text = cls._assistant_content(ev)
    if text:
        pending_reasoning = text
    continue
```

**response 处理**（原 286-295 行）— 合并 pending_reasoning：

```python
if _is_matmaster_source(source) and typ == 'response':
    flush_tool_calls()
    assistant_state_tool_ids.clear()
    last_assistant_text_idx = None
    text = cls._assistant_content(ev)
    if text:
        msg_data = AssistantMessage(
            content=text,
            reasoning_content=pending_reasoning,
        ).model_dump()
        out.append(msg_data)
        last_assistant_text_idx = len(out) - 1
        response_seen_in_turn = True
    pending_reasoning = None
    continue
```

**run_result 处理**（原 349-359 行）— 合并 pending_reasoning 作为兜底：

```python
if _is_matmaster_source(source) and typ in ('run_result', 'finish'):
    flush_tool_calls()
    assistant_state_tool_ids.clear()
    last_assistant_text_idx = None
    if response_seen_in_turn:
        pending_reasoning = None
        continue
    text = cls._assistant_content(ev)
    if text or pending_reasoning:
        msg_data = AssistantMessage(
            content=text or "",
            reasoning_content=pending_reasoning,
        ).model_dump()
        out.append(msg_data)
        last_assistant_text_idx = len(out) - 1
    pending_reasoning = None
    continue
```

**assistant_state 处理**（原 297-324 行）— 清除 pending_reasoning：

在 `assistant_state` 分支中加入 `pending_reasoning = None`。`assistant_state` 包含完整的 AssistantMessage（含 tool_calls 和 reasoning_content），不需要外部缓冲的 reasoning：

```python
if _is_matmaster_source(source) and typ == 'assistant_state':
    flush_tool_calls()
    pending_reasoning = None  # assistant_state 已包含完整消息
    raw_content = ev.get('content')
    ...
```

**turn 边界重置**（query 处理中，原 267-274 行）— flush 残留缓存：

如果上一个 turn 的 thought 没有被 response/run_result/assistant_state 消费（异常路径），在 turn 边界 flush 为独立消息：

```python
if source == 'User' and typ == 'query':
    if pending_reasoning:
        out.append(AssistantMessage(
            content="", reasoning_content=pending_reasoning
        ).model_dump())
        pending_reasoning = None
    flush_tool_calls()
    ...
    pending_reasoning = None
```

**向后兼容性**：此改动同时改善了现有 direct 模式的行为。改前 direct 模式会为 thought 和 run_result 各创建一个 AssistantMessage（重复）；改后合并为 `AssistantMessage(reasoning_content=thinking, content=final_content)`（正确）。

## Data Flow

### Live 推送（agent 执行期间）

```
_call_llm 检测段落边界
  → run_on_segment_complete(hooks, "thought"|"response", content, stream_id)
    → EventEmitterHook.on_segment_complete()
      → bus.emit(ThoughtEvent/ResponseEvent(stream_state="complete", ...))
        → EventRouter 分发
          → PersistenceHandler: "complete" ∉ _STREAMING_STATES → 持久化 ✓
          → SSEHandler: stream_state="complete" → 跳过 ✗（前端已有）
```

### 断线恢复（重连回放）

```
stream_service.py: generate_subscribe_stream()
  → get_session_events() 读取 DB
    → 包含 thought/response(stream_state="complete") 事件
  → _should_emit_event_to_sse(): thought/response 不在 skip list → 回放 ✓
    → 前端收到完整段落内容，渲染断线期间的 thinking/response
```

### 多轮历史（chat_history.py）

```
events_to_dialog_messages()
  → thought(complete) → 缓存为 pending_reasoning
  → response(complete) → 合并为 AssistantMessage(reasoning_content=pending, content=response)
  → run_result → response_seen_in_turn=True → 跳过（去重）✓
```

## Files Changed

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `matmaster/core/agent.py` | 修改 | `_call_llm` 增加边界检测和 `run_on_segment_complete` 调用 |
| `matmaster/core/hooks.py` | 修改 | Hook Protocol + BaseHook + runner + EventEmitterHook 实现 + 文档字符串 |
| `matmaster/integration/sse_handler.py` | 修改 | `_should_skip` 跳过 `stream_state="complete"` |
| `matmaster/types/events.py` | 修改 | stream_state 注释加入 `"complete"` |
| `evomaster/utils/types.py` | 修改 | AssistantMessage 添加 `reasoning_content` 字段 |
| `src/services/chat_history.py` | 修改 | thought 缓存 + response/run_result/assistant_state 合并逻辑 |

## Files NOT Changed (by design)

| 文件 | 原因 |
|------|------|
| `matmaster/integration/persistence_handler.py` | `"complete"` 不在 `_STREAMING_STATES` 中，自动持久化 |
| `matmaster/integration/event_payloads.py` | thought/response 已有内容映射（fallback 路径） |
| `src/services/stream_service.py` | thought/response 已正常回放 |

## Edge Cases

- LLM 只产出 reasoning 无 content（如被中断）：finally 块捕获，发射 thought 段落完成；chat_history 中 pending_reasoning 在 run_result 时合并为 `AssistantMessage(reasoning_content=reasoning, content="")`
- LLM 只产出 content 无 reasoning（模型不支持 extended thinking）：正常检测 content 段落完成；pending_reasoning 为 None，AssistantMessage 无 reasoning_content
- 空内容段落（`content_parts` 为空列表）：`"".join([])` 得到空字符串，仍发射事件（保留审计记录）；chat_history 中空字符串为 falsy，不创建 AssistantMessage
- 流异常中断（provider 抛异常）：finally 块确保已产出的段落仍被持久化
- reasoning → tool_calls（无 content 段落）：reasoning 段落由 finally 块持久化；chat_history 中 pending_reasoning 在 assistant_state 分支被显式清除（assistant_state 包含完整消息）
- 同一 chunk 携带 reasoning_content + content：处理顺序约束确保先累积 reasoning 再检测转换，最后一个 reasoning token 不丢失
- run_result 与 response(complete) 重复：chat_history.py 的 response_seen_in_turn 去重逻辑防止重复 Message
- direct 模式向后兼容：thought 缓存 + run_result 合并产出 `AssistantMessage(reasoning_content=thinking, content=response)`，比改前（两个独立 AssistantMessage）更正确
