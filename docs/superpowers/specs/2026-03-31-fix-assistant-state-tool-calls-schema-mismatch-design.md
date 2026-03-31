# Fix assistant_state tool_calls Schema Mismatch

**Date:** 2026-03-31
**Status:** Draft
**Scope:** Bug fix — `src/services/chat_history.py`

## Problem

存在两条 `assistant_state` 持久化路径，各自使用不同的序列化格式：

1. **matmaster `AssistantStateHook`** (`matmaster/hooks/assistant_state.py`) — 使用 `matmaster.types.messages.AssistantMessage.model_dump(mode="json")`，产出扁平 `ToolCallData` 格式：
   ```json
   {"id": "...", "name": "...", "arguments": {...}}
   ```

2. **playground `stream_agent`** (`playground/mat_master/service/stream_agent.py:127`) — 使用 `evomaster.utils.types.AssistantMessage.model_dump()`，产出嵌套 `ToolCall` 格式：
   ```json
   {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
   ```

`chat_history.py` 用 evomaster 的 `AssistantMessage.model_validate()` 反序列化，只兼容路径 2 的格式。路径 1 的数据验证失败 → `assistant_state` 事件被跳过 → tool_calls 从历史中丢失 → 产生孤儿 tool messages → LLM API 400 "Invalid role of message"。

注：两种格式的 `role` 字段值均为字符串 `"assistant"`，Pydantic `model_validate` 会自动转为 enum，无需适配。matmaster 序列化数据无 `meta` 字段，evomaster model 会用默认值 `{}`，也无需特殊处理。

## Solution: Adapter Function

在 `chat_history.py` 中添加 `_adapt_tool_calls_format(raw: dict) -> dict`，在 `model_validate()` 之前将 matmaster 扁平格式转为 evomaster 嵌套格式。

### Adapter Logic

```python
def _adapt_tool_calls_format(raw: dict) -> dict:
    """Adapt matmaster flat ToolCallData format to evomaster nested ToolCall format.

    matmaster serializes tool_calls as: {"id", "name", "arguments": dict}
    evomaster expects:               {"id", "type", "function": {"name", "arguments": str}}

    If a tool_call already has a 'function' key, it is left as-is (already evomaster format).
    """
    tcs = raw.get('tool_calls')
    if not tcs or not isinstance(tcs, list):
        return raw
    adapted = []
    for tc in tcs:
        if not isinstance(tc, dict):
            adapted.append(tc)
            continue
        if 'function' in tc:
            adapted.append(tc)
        elif 'name' in tc and 'arguments' in tc:
            args = tc['arguments']
            if isinstance(args, dict):
                args_str = json.dumps(args, ensure_ascii=False)
            elif isinstance(args, str):
                args_str = args
            else:
                args_str = '{}'
            adapted.append({
                'id': tc.get('id', ''),
                'type': 'function',
                'function': {
                    'name': tc['name'],
                    'arguments': args_str,
                },
            })
        else:
            adapted.append(tc)
    return {**raw, 'tool_calls': adapted}
```

### Call Site

In `events_to_dialog_messages()`, line ~391:

```python
# Before:
msg = AssistantMessage.model_validate(raw_content or {})

# After:
msg = AssistantMessage.model_validate(
    _adapt_tool_calls_format(raw_content) if raw_content else {}
)
```

## Why This Approach

- Minimal change: one new function + one line modified
- No cross-module import changes
- Backward compatible: handles both evomaster 嵌套格式和 matmaster 扁平格式（DB 中两种并存）
- Isolated: only affects `assistant_state` deserialization path
- Does not alter `AssistantStateHook` serialization behavior

## What This Does NOT Change

- All other message construction in `chat_history.py` continues using evomaster types
- `events_to_messages()` conversion pipeline unchanged — 其输入格式（嵌套 ToolCall dict）不受影响
- `AssistantStateHook` serialization unchanged
- No schema migration needed for existing DB data

## Testing

- Unit test: `_adapt_tool_calls_format` with matmaster flat input → evomaster nested output
- Unit test: already-evomaster format input passes through unchanged
- Unit test: edge cases (no tool_calls, empty list, None, arguments 为 None/str/dict)
- Integration: verify `events_to_dialog_messages()` succeeds with matmaster-format `assistant_state` events
- Update existing test `test_chat_history_avoids_duplicate_tool_calls_when_assistant_state_exists` to use matmaster flat format input, ensuring the fixed path is covered
- End-to-end: verify `events_to_messages()` produces correct matmaster Message objects when upstream `assistant_state` events use flat format
