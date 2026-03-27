"""Tests for ChatHistoryConverter._repair_incomplete_tool_turns()."""

from src.services.chat_history import ChatHistoryConverter


def _assistant_with_tool_calls(tool_calls: list[dict]) -> dict:
    return {
        'role': 'assistant',
        'content': '',
        'tool_calls': tool_calls,
    }


def _tool_call(call_id: str, name: str, args: str = '{}') -> dict:
    return {
        'id': call_id,
        'type': 'function',
        'function': {'name': name, 'arguments': args},
    }


def _tool_message(call_id: str, name: str = 'tool', content: str = 'ok') -> dict:
    return {
        'role': 'tool',
        'tool_call_id': call_id,
        'name': name,
        'content': content,
    }


def test_repair_total_missing():
    """All tool_results missing — should synthesize all."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c2', 'read'),
            _tool_call('c3', 'glob'),
        ]),
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    assert len(result) == 5
    for i, (call_id, name) in enumerate([('c1', 'bash'), ('c2', 'read'), ('c3', 'glob')]):
        tm = result[2 + i]
        assert tm['role'] == 'tool'
        assert tm['tool_call_id'] == call_id
        assert 'interrupted' in tm['content'].lower()
        assert name in tm['content']


def test_repair_partial_missing():
    """2 of 3 tool_calls have results — should synthesize only the missing one."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c2', 'read'),
            _tool_call('c3', 'glob'),
        ]),
        _tool_message('c1', 'bash', 'output1'),
        _tool_message('c2', 'read', 'output2'),
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    assert len(result) == 5
    assert result[2]['tool_call_id'] == 'c1'
    assert result[3]['tool_call_id'] == 'c2'
    assert result[4]['role'] == 'tool'
    assert result[4]['tool_call_id'] == 'c3'
    assert 'interrupted' in result[4]['content'].lower()
    assert 'glob' in result[4]['content']


def test_repair_noop_when_complete():
    """All tool_calls have matching results — no change."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c2', 'read'),
        ]),
        _tool_message('c1', 'bash', 'ok1'),
        _tool_message('c2', 'read', 'ok2'),
        _assistant_with_tool_calls([
            _tool_call('c3', 'glob'),
        ]),
        _tool_message('c3', 'glob', 'ok3'),
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    assert result == messages


def test_repair_noop_no_tool_calls():
    """No tool_calls in history — no change."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        {'role': 'assistant', 'content': 'hi there'},
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    assert result == messages


def test_repair_multiple_incomplete_turns():
    """Two separate incomplete tool turns — both should be repaired."""
    messages = [
        {'role': 'user', 'content': 'q1'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c2', 'read'),
        ]),
        _tool_message('c1', 'bash', 'ok'),
        # c2 missing
        {'role': 'user', 'content': 'q2'},
        _assistant_with_tool_calls([
            _tool_call('c3', 'glob'),
        ]),
        # c3 missing
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    assert result[3]['role'] == 'tool'
    assert result[3]['tool_call_id'] == 'c2'
    assert 'interrupted' in result[3]['content'].lower()
    assert result[6]['role'] == 'tool'
    assert result[6]['tool_call_id'] == 'c3'
    assert 'interrupted' in result[6]['content'].lower()


def test_repair_deduplicates_tool_call_ids():
    """Duplicate IDs in tool_calls — should only synthesize one."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c1', 'bash'),
        ]),
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    synthetic = [m for m in result if m['role'] == 'tool']
    assert len(synthetic) == 1
    assert synthetic[0]['tool_call_id'] == 'c1'


def test_repair_preserves_tool_call_order():
    """Synthetic messages follow the order in tool_calls."""
    messages = [
        {'role': 'user', 'content': 'hello'},
        _assistant_with_tool_calls([
            _tool_call('c1', 'bash'),
            _tool_call('c2', 'read'),
            _tool_call('c3', 'glob'),
        ]),
        _tool_message('c2', 'read', 'ok'),
    ]
    result = ChatHistoryConverter._repair_incomplete_tool_turns(messages)
    tool_ids = [m['tool_call_id'] for m in result if m['role'] == 'tool']
    assert tool_ids == ['c1', 'c2', 'c3']


def test_events_to_dialog_messages_repairs_incomplete_turn():
    """End-to-end: events with missing tool_result produce valid message list."""
    events = [
        {'source': 'User', 'type': 'query', 'content': 'do stuff'},
        {
            'source': 'MatMaster',
            'type': 'tool_call',
            'content': {'id': 'c1', 'call_id': 'c1', 'name': 'bash', 'args': {}},
        },
        {
            'source': 'MatMaster',
            'type': 'tool_call',
            'content': {'id': 'c2', 'call_id': 'c2', 'name': 'read', 'args': {}},
        },
        {
            'source': 'MatMaster',
            'type': 'tool_result',
            'content': {
                'id': 'c1',
                'call_id': 'c1',
                'name': 'bash',
                'result': 'done',
                'status': 'success',
            },
        },
        # c2 tool_result missing (interrupted)
    ]

    msgs = ChatHistoryConverter.events_to_dialog_messages(events)

    # user + assistant(tool_calls) + tool(c1) + synthetic_tool(c2)
    assert len(msgs) == 4
    assert msgs[0]['role'] == 'user'
    assert msgs[1]['role'] == 'assistant'
    assert len(msgs[1].get('tool_calls', [])) == 2
    assert msgs[2]['tool_call_id'] == 'c1'
    assert msgs[3]['tool_call_id'] == 'c2'
    assert 'interrupted' in str(msgs[3]['content']).lower()
