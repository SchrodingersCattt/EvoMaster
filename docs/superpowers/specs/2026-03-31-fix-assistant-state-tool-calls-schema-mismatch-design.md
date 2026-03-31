# Fix assistant_state tool_calls Schema Mismatch

**Date:** 2026-03-31
**Status:** Draft
**Scope:** Bug fix — `src/services/chat_history.py`

## Problem

`AssistantStateHook` (matmaster) serializes assistant messages with flat `ToolCallData` format:
```json
{"id": "...", "name": "...", "arguments": {...}}
```

`chat_history.py` deserializes using evomaster's `AssistantMessage.model_validate()` which expects nested `ToolCall` format:
```json
{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
```

Validation fails → `assistant_state` events are skipped → tool_calls lost from history → orphaned tool messages → LLM API rejects with 400 "Invalid role of message".

## Solution: Adapter Function

Add `_adapt_tool_calls_format(raw: dict) -> dict` in `chat_history.py` that normalizes matmaster flat format to evomaster nested format before `model_validate()`.

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
            adapted.append({
                'id': tc.get('id', ''),
                'type': 'function',
                'function': {
                    'name': tc['name'],
                    'arguments': json.dumps(args) if isinstance(args, dict) else str(args),
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
- Backward compatible: handles both old evomaster-format and new matmaster-format data in DB
- Isolated: only affects `assistant_state` deserialization path
- Does not alter `AssistantStateHook` serialization behavior

## What This Does NOT Change

- All other message construction in `chat_history.py` continues using evomaster types
- `events_to_messages()` conversion pipeline unchanged
- `AssistantStateHook` serialization unchanged
- No schema migration needed for existing DB data

## Testing

- Unit test: `_adapt_tool_calls_format` with matmaster flat input → evomaster nested output
- Unit test: already-evomaster format input passes through unchanged
- Unit test: edge cases (no tool_calls, empty list, None)
- Integration: verify `events_to_dialog_messages()` succeeds with matmaster-format `assistant_state` events
