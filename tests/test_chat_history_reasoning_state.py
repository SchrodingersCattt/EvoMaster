from evomaster.utils.types import AssistantMessage, FunctionCall, ToolCall
from playground.mat_master.service.stream_agent import StreamingMatMasterAgent
from src.services.chat_history import ChatHistoryConverter


def test_chat_history_prefers_assistant_state_when_present():
    events = [
        {
            'source': 'MatMaster',
            'type': 'assistant_state',
            'content': {
                'role': 'assistant',
                'content': '',
                'meta': {'reasoning_content': 'r'},
                'tool_calls': [],
            },
        }
    ]

    msgs = ChatHistoryConverter.events_to_dialog_messages(events)

    assert msgs[0]['meta']['reasoning_content'] == 'r'


def test_chat_history_avoids_duplicate_tool_calls_when_assistant_state_exists():
    events = [
        {'source': 'MatMaster', 'type': 'thought', 'content': 'real reasoning'},
        {
            'source': 'MatMaster',
            'type': 'assistant_state',
            'content': {
                'role': 'assistant',
                'content': '',
                'meta': {'reasoning_content': 'real reasoning'},
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
    assert msgs[0]['tool_calls'][0]['id'] == 'call_1'
    assert msgs[1]['tool_call_id'] == 'call_1'


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
                'result': '/home/user',
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


def test_streaming_agent_emits_private_assistant_state_event():
    events: list[dict] = []
    agent = StreamingMatMasterAgent.__new__(StreamingMatMasterAgent)
    agent.event_callback = lambda source, event_type, content, **extra: events.append(
        {
            'source': source,
            'type': event_type,
            'content': content,
            **extra,
        }
    )
    agent._agent_name = 'Coder'

    assistant = AssistantMessage(
        content='',
        meta={'reasoning_content': 'r'},
        tool_calls=[
            ToolCall(
                id='call_1',
                function=FunctionCall(
                    name='execute_bash',
                    arguments='{"command":"pwd"}',
                ),
            )
        ],
    )

    agent._on_assistant_message(assistant)

    assistant_state_event = next(
        event for event in events if event['type'] == 'assistant_state'
    )
    assert assistant_state_event['content']['meta']['reasoning_content'] == 'r'


def test_exclude_spawn_events_omits_subagent_from_dialog_messages():
    """Sub-agent rows must not appear in parent dialog reconstruction."""
    events = [
        {'source': 'User', 'type': 'query', 'content': 'hi', 'task_id': 't1'},
        {
            'source': 'MatMaster',
            'type': 'response',
            'content': 'from subagent',
            'task_id': 't1',
            'spawn_id': 'sp-1',
        },
    ]
    filtered = ChatHistoryConverter.exclude_spawn_events(events)
    msgs = ChatHistoryConverter.events_to_dialog_messages(filtered)
    assert len(msgs) == 1
    assert msgs[0]['role'] == 'user'
