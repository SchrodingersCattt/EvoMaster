from evomaster.utils.llm import LLMConfig


def test_llm_config_accepts_thinking_effort():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='medium',
    )
    assert cfg.thinking_effort == 'medium'
