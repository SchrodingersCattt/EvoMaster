from evomaster.utils.types import AssistantMessage, Dialog


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
