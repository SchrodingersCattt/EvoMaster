# Fix assistant_state tool_calls Schema Mismatch — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `chat_history.py` to accept both matmaster flat and evomaster nested tool_calls formats in `assistant_state` events, eliminating 400 errors caused by orphaned tool messages.

**Architecture:** Add a private adapter function `_adapt_tool_calls_format()` that normalizes matmaster's flat `ToolCallData` format to evomaster's nested `ToolCall` format before Pydantic validation. Called at exactly one site: the `assistant_state` deserialization in `events_to_dialog_messages()`.

**Tech Stack:** Python, Pydantic v2, pytest

---

## Chunk 1: Implementation and Tests

### Task 1: Add unit tests for `_adapt_tool_calls_format`

**Files:**
- Create: `tests/test_adapt_tool_calls_format.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for _adapt_tool_calls_format in chat_history."""

import json

from src.services.chat_history import _adapt_tool_calls_format


class TestAdaptToolCallsFormat:
    """Tests for the tool_calls format adapter."""

    def test_matmaster_flat_format_converted_to_nested(self):
        """Matmaster ToolCallData flat format → evomaster ToolCall nested format."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'execute_bash',
                    'arguments': {'command': 'pwd'},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        tc = result['tool_calls'][0]
        assert tc['id'] == 'call_1'
        assert tc['type'] == 'function'
        assert tc['function']['name'] == 'execute_bash'
        assert json.loads(tc['function']['arguments']) == {'command': 'pwd'}

    def test_evomaster_nested_format_passthrough(self):
        """Already-nested evomaster format passes through unchanged."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {
                        'name': 'execute_bash',
                        'arguments': '{"command":"pwd"}',
                    },
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0] is raw['tool_calls'][0]

    def test_no_tool_calls_passthrough(self):
        """Dict without tool_calls returns as-is."""
        raw = {'role': 'assistant', 'content': 'hello'}
        assert _adapt_tool_calls_format(raw) is raw

    def test_empty_tool_calls_list_passthrough(self):
        """Empty tool_calls list returns as-is."""
        raw = {'role': 'assistant', 'content': '', 'tool_calls': []}
        assert _adapt_tool_calls_format(raw) is raw

    def test_none_tool_calls_passthrough(self):
        """None tool_calls returns as-is."""
        raw = {'role': 'assistant', 'content': '', 'tool_calls': None}
        assert _adapt_tool_calls_format(raw) is raw

    def test_arguments_already_string(self):
        """String arguments passed through without double-encoding."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'bash',
                    'arguments': '{"cmd":"ls"}',
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['function']['arguments'] == '{"cmd":"ls"}'

    def test_arguments_none_fallback(self):
        """None arguments produce empty JSON object string."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {'id': 'call_1', 'name': 'bash', 'arguments': None}
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['tool_calls'][0]['function']['arguments'] == '{}'

    def test_unicode_arguments_preserved(self):
        """Non-ASCII arguments preserved with ensure_ascii=False."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'name': 'search',
                    'arguments': {'query': '分子动力学'},
                }
            ],
        }
        result = _adapt_tool_calls_format(raw)
        args_str = result['tool_calls'][0]['function']['arguments']
        assert '分子动力学' in args_str
        assert json.loads(args_str) == {'query': '分子动力学'}

    def test_mixed_formats_in_same_list(self):
        """List with both flat and nested items — each handled correctly."""
        raw = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {'name': 'a', 'arguments': '{}'},
                },
                {
                    'id': 'call_2',
                    'name': 'b',
                    'arguments': {'x': 1},
                },
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert 'function' in result['tool_calls'][0]
        assert result['tool_calls'][0] is raw['tool_calls'][0]
        assert result['tool_calls'][1]['function']['name'] == 'b'

    def test_non_dict_original_unchanged(self):
        """Other top-level fields in raw dict are preserved."""
        raw = {
            'role': 'assistant',
            'content': 'text',
            'reasoning_content': 'think',
            'tool_calls': [
                {'id': 'c1', 'name': 'x', 'arguments': {}}
            ],
        }
        result = _adapt_tool_calls_format(raw)
        assert result['role'] == 'assistant'
        assert result['content'] == 'text'
        assert result['reasoning_content'] == 'think'
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && python -m pytest tests/test_adapt_tool_calls_format.py -v`
Expected: FAIL with `ImportError: cannot import name '_adapt_tool_calls_format'`

### Task 2: Implement `_adapt_tool_calls_format` and wire into call site

**Files:**
- Modify: `src/services/chat_history.py:49` (add function after `_summarize_assistant_state_content_for_log`)
- Modify: `src/services/chat_history.py:391` (change `model_validate` call)

- [ ] **Step 3: Add `_adapt_tool_calls_format` function**

Insert between `_summarize_assistant_state_content_for_log` (ends at line 49) and `_serialized_message_role` (line 51). Note: `json` is already imported at line 3, no additional import needed.

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

- [ ] **Step 4: Wire adapter into call site**

In `events_to_dialog_messages()`, change line 391 from:
```python
                    msg = AssistantMessage.model_validate(raw_content or {})
```
to:
```python
                    msg = AssistantMessage.model_validate(
                        _adapt_tool_calls_format(raw_content) if raw_content else {}
                    )
```

- [ ] **Step 5: Run unit tests — expect all PASS**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && python -m pytest tests/test_adapt_tool_calls_format.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit adapter + unit tests**

```bash
git add src/services/chat_history.py tests/test_adapt_tool_calls_format.py
git commit -m "fix: adapt matmaster flat tool_calls format in chat_history deserialization"
```

### Task 3: Update existing test and add integration tests for matmaster format

**Files:**
- Modify: `tests/test_chat_history_reasoning_state.py:25-71` (existing imports at line 1-3 already include `ChatHistoryConverter`)

- [ ] **Step 7: Convert existing dedup test to use matmaster flat format**

In `test_chat_history_avoids_duplicate_tool_calls_when_assistant_state_exists` (line 35-44), change the `tool_calls` from evomaster nested format to matmaster flat format. This verifies the `assistant_state_tool_ids` dedup logic works with flat input:

```python
# Replace lines 35-44 (the nested tool_calls block) with:
                'tool_calls': [
                    {
                        'id': 'call_1',
                        'name': 'execute_bash',
                        'arguments': {'command': 'pwd'},
                    }
                ],
```

- [ ] **Step 8: Add new integration test for `events_to_dialog_messages()`**

Add after `test_chat_history_avoids_duplicate_tool_calls_when_assistant_state_exists` (after line 71):

```python
def test_chat_history_handles_matmaster_flat_tool_calls_in_assistant_state():
    """assistant_state with matmaster flat ToolCallData format is accepted."""
    events = [
        {'source': 'MatMaster', 'type': 'thought', 'content': 'reasoning'},
        {
            'source': 'MatMaster',
            'type': 'assistant_state',
            'content': {
                'role': 'assistant',
                'content': '',
                'reasoning_content': 'reasoning',
                'tool_calls': [
                    {
                        'id': 'call_1',
                        'name': 'execute_bash',
                        'arguments': {'command': 'pwd'},
                    }
                ],
            },
        },
        {
            'source': 'MatMaster',
            'type': 'tool_call',
            'content': {
                'id': 'call_1',
                'name': 'execute_bash',
                'args': {'command': 'pwd'},
            },
        },
        {
            'source': 'MatMaster',
            'type': 'tool_result',
            'content': {
                'id': 'call_1',
                'name': 'execute_bash',
                'result': {'message': 'ok'},
            },
        },
    ]

    msgs = ChatHistoryConverter.events_to_dialog_messages(events)

    assert len(msgs) == 2
    assert msgs[0]['role'] == 'assistant'
    assert msgs[0]['tool_calls'][0]['id'] == 'call_1'
    assert msgs[0]['tool_calls'][0]['function']['name'] == 'execute_bash'
    assert msgs[1]['role'] == 'tool'
    assert msgs[1]['tool_call_id'] == 'call_1'
```

- [ ] **Step 9: Add end-to-end test for `events_to_messages()` with flat format**

Add after the test above:

```python
def test_events_to_messages_with_matmaster_flat_tool_calls():
    """events_to_messages() produces correct matmaster Message objects from flat tool_calls."""
    events = [
        {'source': 'MatMaster', 'type': 'thought', 'content': 'reasoning'},
        {
            'source': 'MatMaster',
            'type': 'assistant_state',
            'content': {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': 'call_1',
                        'name': 'execute_bash',
                        'arguments': {'command': 'pwd'},
                    }
                ],
            },
        },
        {
            'source': 'MatMaster',
            'type': 'tool_call',
            'content': {
                'id': 'call_1',
                'name': 'execute_bash',
                'args': {'command': 'pwd'},
            },
        },
        {
            'source': 'MatMaster',
            'type': 'tool_result',
            'content': {
                'id': 'call_1',
                'name': 'execute_bash',
                'result': {'message': 'ok'},
            },
        },
    ]

    msgs = ChatHistoryConverter.events_to_messages(events)

    assert len(msgs) == 2
    assistant_msg = msgs[0]
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0].id == 'call_1'
    assert assistant_msg.tool_calls[0].name == 'execute_bash'
    assert assistant_msg.tool_calls[0].arguments == {'command': 'pwd'}
    assert msgs[1].tool_call_id == 'call_1'
```

- [ ] **Step 10: Run full test suite for chat_history**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && python -m pytest tests/test_chat_history_reasoning_state.py tests/test_adapt_tool_calls_format.py -v`
Expected: All tests PASS

- [ ] **Step 11: Commit integration tests**

```bash
git add tests/test_chat_history_reasoning_state.py
git commit -m "test: add integration and e2e tests for matmaster flat tool_calls in assistant_state"
```

### Task 4: Run broader test suite to verify no regressions

- [ ] **Step 12: Run all chat_history related tests**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && python -m pytest tests/ -k "chat_history" -v`
Expected: All PASS, no regressions

- [ ] **Step 13: Run pre-commit checks**

Run: `cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && pre-commit run --all-files`
Expected: All checks pass (black, ruff, mypy)
