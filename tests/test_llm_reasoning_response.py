from evomaster.utils.llm import LLMResponse


def test_llm_response_to_assistant_message_preserves_reasoning():
    response = LLMResponse(
        content='final answer',
        reasoning_content='hidden reasoning',
        api_message_extras={'reasoning_content': 'hidden reasoning'},
    )
    msg = response.to_assistant_message()
    assert msg.content == 'final answer'
    assert msg.meta['reasoning_content'] == 'hidden reasoning'
    assert msg.meta['api_message_extras']['reasoning_content'] == 'hidden reasoning'
