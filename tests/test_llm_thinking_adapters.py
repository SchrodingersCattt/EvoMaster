from evomaster.utils.llm import LLMConfig, _build_reasoning_request_overrides


def test_llm_config_accepts_thinking_effort():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='medium',
    )
    assert cfg.thinking_effort == 'medium'


def test_claude_46_maps_to_adaptive_thinking_and_output_effort():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='medium',
    )
    req = _build_reasoning_request_overrides(cfg)
    assert req['extra_body']['thinking'] == {'type': 'adaptive'}
    assert req['extra_body']['output_config']['effort'] == 'medium'


def test_gpt5_maps_to_reasoning_effort():
    cfg = LLMConfig(
        provider='openai',
        model='azure/gpt-5',
        api_key='dummy',
        base_url='https://azure.example.com',
        thinking_effort='medium',
    )
    req = _build_reasoning_request_overrides(cfg)
    assert req['reasoning_effort'] == 'medium'
