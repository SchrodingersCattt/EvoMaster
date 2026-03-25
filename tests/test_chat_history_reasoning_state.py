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
                        'type': 'function',
                        'function': {
                            'name': 'execute_bash',
                            'arguments': '{"command":"pwd"}',
                        },
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
