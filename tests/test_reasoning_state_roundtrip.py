from evomaster.utils.types import AssistantMessage, Dialog, FunctionCall, ToolCall


def test_assistant_api_message_extras_roundtrip_into_api_messages():
    msg = AssistantMessage(
        content='',
        meta={
            'api_message_extras': {
                'thinking_blocks': [{'type': 'thinking', 'thinking': 'x'}]
            }
        },
    )
    dialog = Dialog(messages=[msg])
    api_messages = dialog.get_messages_for_api()

    assert api_messages[0]['thinking_blocks'][0]['thinking'] == 'x'


def test_assistant_empty_content_with_tool_calls_is_omitted_from_api_messages():
    msg = AssistantMessage(
        content='',
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

    api_messages = Dialog(messages=[msg]).get_messages_for_api()

    assert 'content' not in api_messages[0]
    assert api_messages[0]['tool_calls'][0]['id'] == 'call_1'


def test_assistant_api_message_extras_prune_blank_reasoning_blocks():
    msg = AssistantMessage(
        content='',
        meta={
            'api_message_extras': {
                'thinking_blocks': [
                    {'type': 'thinking', 'thinking': 'alpha'},
                    {'type': 'thinking', 'thinking': ''},
                    {'type': 'thinking', 'thinking': '', 'signature': 'sig'},
                ],
                'content': [
                    {'type': 'thinking', 'thinking': 'beta'},
                    {'type': 'text', 'text': ''},
                    {'type': 'text', 'text': 'final'},
                ],
                'provider_specific_fields': {
                    'reasoningContent': {'text': '', 'signature': 'sig2'}
                },
            }
        },
    )

    api_messages = Dialog(messages=[msg]).get_messages_for_api()
    extras_message = api_messages[0]

    assert extras_message['thinking_blocks'] == [
        {'type': 'thinking', 'thinking': 'alpha'}
    ]
    assert extras_message['content'] == [
        {'type': 'thinking', 'thinking': 'beta'},
        {'type': 'text', 'text': 'final'},
    ]
    assert 'reasoningContent' not in extras_message.get('provider_specific_fields', {})
