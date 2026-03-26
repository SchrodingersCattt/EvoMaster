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
- 不改动 DB schema（`evo_chat_events` 仍只存 `type/source/content/task_id/...`）
- 不处理跨 turn 的段落合并（每次 `_call_llm` 调用独立产生段落事件）

## Key Design Decision: 复用已有事件类型 + stream_state="complete"（仅 live bus 可见）

不引入新的 `SegmentCompleteEvent`，而是复用 `ThoughtEvent` / `ResponseEvent`，用 `stream_state="complete"` 标记段落完成。该标记只用于 **live bus 路由**，不会落到 DB 列结构中；DB 仍只持久化既有的 `type + public content`。理由：

1. **PersistenceHandler** 的 `_STREAMING_STATES = {"start", "streaming", "end"}` 不含 `"complete"` → 自动持久化，无需改动
2. **回放路径** 仍复用 thought/response 既有事件类型，无需引入新的 payload shape；但需在 `stream_service.py` 增加去重逻辑，避免 `response(complete)` 与 `run_result` 双重回放
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

### 6. assistant_state reasoning 保真（`matmaster/hooks/assistant_state.py`）

`AssistantStateHook` 当前使用 `last_assistant.to_api_dict()` 持久化 tool-use turn 的 assistant state。该序列化不包含 `reasoning_content`，会让 `reasoning -> tool_calls` 场景中的 reasoning 在历史重建时丢失。

需要改为持久化完整消息：

```python
AssistantStateEvent(
    source=self._source,
    state=last_assistant.model_dump(mode="json"),
)
```

这样 `assistant_state` 事件可直接携带 `reasoning_content`。

### 7. AssistantMessage 类型扩展（`evomaster/utils/types.py`）

`chat_history.py` 导入的是 `evomaster.utils.types.AssistantMessage`，该类当前只有 `content` 和 `tool_calls` 字段，没有 `reasoning_content`。需要添加：

```python
class AssistantMessage(BaseMessage):
    role: MessageRole = MessageRole.ASSISTANT
    tool_calls: list[ToolCall] | None = Field(default=None, description='工具调用列表')
    reasoning_content: str | None = None
```

不添加此字段会导致下方 chat_history.py 合并逻辑中 `reasoning_content=pending_reasoning` 参数被 Pydantic 静默忽略，reasoning 内容丢失。

### 8. chat_history.py thought 缓存合并（`src/services/chat_history.py`）

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

**assistant_state 处理**（原 297-324 行）— 优先使用 assistant_state 自带 reasoning，缺失时合并 pending_reasoning：

`assistant_state` 来自两种历史来源：
- 新路径：`AssistantStateHook.model_dump(mode="json")`，顶层包含 `reasoning_content`
- 旧路径：legacy `assistant_state` 可能只有 `meta.reasoning_content`，甚至完全没有 reasoning

因此不能无条件清空 `pending_reasoning`，而应在 `assistant_state` 缺少 reasoning 时补进去：

```python
if _is_matmaster_source(source) and typ == 'assistant_state':
    flush_tool_calls()
    raw_content = ev.get('content')
    ...
    assistant_reasoning = cls._assistant_reasoning_content(raw_content)
    if pending_reasoning and not assistant_reasoning:
        msg = msg.model_copy(update={'reasoning_content': pending_reasoning})
    ...
    pending_reasoning = None
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

**补充**：`events_to_messages()` 还需把序列化 dict 中的 `reasoning_content`（以及 legacy `meta.reasoning_content`）继续传给 `matmaster.types.messages.AssistantMessage`，否则 reasoning 只能存在于 dialog dict，进不到 runtime Message 层。

**向后兼容性**：此改动同时改善了现有 direct 模式的行为。改前 direct 模式会为 thought 和 run_result 各创建一个 AssistantMessage（重复）；改后合并为 `AssistantMessage(reasoning_content=thinking, content=final_content)`（正确）。

### 9. replay 去重（`src/services/stream_service.py`）

因为新增了持久化的 `response(complete)`，断线重连时若继续原样回放 DB，将会同时看到：
- `response`：完整最终答案
- `run_result`：同一轮的最终答案包装

需要在 replay 前做轻量去重：

```python
def _dedupe_replayed_terminal_events(events: list[dict]) -> list[dict]:
    ...
```

规则：若某个 `task_id` 的上一个 **可回放事件** 已经是 `response`，则跳过该 task 的 `run_result` / `finish`。这样：
- 最终答案不会双重回放
- 仍保留 tool-use turn 中依赖 `run_result` 兜底的旧数据

## Data Flow

### Live 推送（agent 执行期间）

```
_call_llm 检测段落边界
  → run_on_segment_complete(hooks, "thought"|"response", content, stream_id)
    → EventEmitterHook.on_segment_complete()
      → bus.emit(ThoughtEvent/ResponseEvent(stream_state="complete", ...))
        → EventRouter 分发
          → PersistenceHandler: "complete" ∉ _STREAMING_STATES → 按既有 type/content 契约持久化 ✓
          → SSEHandler: stream_state="complete" → 跳过 ✗（前端已有）
```

### 断线恢复（重连回放）

```
stream_service.py: generate_subscribe_stream()
  → get_session_events() 读取 DB
    → 包含持久化后的 thought/response 内容（DB 不存 stream_state）
  → _dedupe_replayed_terminal_events()：若 response 已是该 task 最后一个可回放事件，则跳过 run_result
  → _should_emit_event_to_sse(): thought/response 不在 skip list → 回放 ✓
    → 前端收到完整段落内容，且不会重复看到最终 run_result
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
| `matmaster/hooks/assistant_state.py` | 修改 | `assistant_state` 改为持久化完整 AssistantMessage，保留 reasoning_content |
| `matmaster/integration/sse_handler.py` | 修改 | `_should_skip` 跳过 `stream_state="complete"` |
| `matmaster/types/events.py` | 修改 | stream_state 注释加入 `"complete"` |
| `evomaster/utils/types.py` | 修改 | AssistantMessage 添加 `reasoning_content` 字段 |
| `src/services/chat_history.py` | 修改 | thought 缓存 + response/run_result/assistant_state 合并逻辑 + reasoning 传递到 matmaster Message 层 |
| `src/services/stream_service.py` | 修改 | replay 前按 task 去重 `response`/`run_result` 最终答案 |

## Files NOT Changed (by design)

| 文件 | 原因 |
|------|------|
| `matmaster/integration/persistence_handler.py` | `"complete"` 不在 `_STREAMING_STATES` 中，自动持久化 |
| `matmaster/integration/event_payloads.py` | thought/response 已有内容映射（fallback 路径） |

## Edge Cases

- LLM 只产出 reasoning 无 content（如被中断）：finally 块捕获，发射 thought 段落完成；chat_history 中 pending_reasoning 在 run_result 时合并为 `AssistantMessage(reasoning_content=reasoning, content="")`
- LLM 只产出 content 无 reasoning（模型不支持 extended thinking）：正常检测 content 段落完成；pending_reasoning 为 None，AssistantMessage 无 reasoning_content
- 空内容段落（`content_parts` 为空列表）：`"".join([])` 得到空字符串，仍发射事件（保留审计记录）；chat_history 中空字符串为 falsy，不创建 AssistantMessage
- 流异常中断（provider 抛异常）：finally 块确保已产出的段落仍被持久化
- reasoning → tool_calls（无 content 段落）：reasoning 段落由 finally 块持久化；新路径下 assistant_state 自带 `reasoning_content`，旧路径下 chat_history 会把 `pending_reasoning` 合并进 assistant_state，避免丢失
- 同一 chunk 携带 reasoning_content + content：处理顺序约束确保先累积 reasoning 再检测转换，最后一个 reasoning token 不丢失
- `stream_state="complete"` 不会持久化到 DB 行结构：Replay 依赖的是 persisted `type=thought/response`，不是 persisted `stream_state`
- run_result 与 response(complete) 重复：chat_history.py 的 `response_seen_in_turn` 防止多轮历史重复 Message，`stream_service.py` 的 replay 去重防止断线恢复重复 SSE frame
- direct 模式向后兼容：thought 缓存 + run_result 合并产出 `AssistantMessage(reasoning_content=thinking, content=response)`，比改前（两个独立 AssistantMessage）更正确
