from evomaster.utils.llm import (
    LLMConfig,
    LLMConfigurationError,
    _build_reasoning_request_overrides,
    _classify_llm_error,
    _normalize_request_params,
)


def test_llm_config_accepts_thinking_effort():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='medium',
    )
    assert cfg.thinking_effort == 'medium'


def test_llm_config_accepts_reasoning_protocol():
    cfg = LLMConfig(
        provider='openai',
        model='claude-prod',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='high',
        reasoning_protocol='anthropic_adaptive_thinking',
    )
    assert cfg.reasoning_protocol == 'anthropic_adaptive_thinking'


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


def test_explicit_reasoning_protocol_overrides_model_name_guess():
    cfg = LLMConfig(
        provider='openai',
        model='proxy-deployment-name',
        api_key='dummy',
        base_url='https://proxy.example.com',
        thinking_effort='high',
        reasoning_protocol='anthropic_adaptive_thinking',
    )
    req = _build_reasoning_request_overrides(cfg)
    assert req['extra_body']['thinking'] == {'type': 'adaptive'}
    assert req['extra_body']['output_config']['effort'] == 'high'


def test_claude_family_forces_temperature_one_when_reasoning_enabled():
    cfg = LLMConfig(
        provider='openai',
        model='proxy-deployment-name',
        api_key='dummy',
        base_url='https://proxy.example.com',
        model_family='claude-4.6',
        reasoning_protocol='anthropic_adaptive_thinking',
        thinking_effort='high',
        temperature=0.2,
    )
    req = _normalize_request_params(cfg, {'temperature': 0.2})
    assert req['temperature'] == 1


def test_classify_combined_claude_configuration_error():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        model_family='claude-4.6',
        reasoning_protocol='anthropic_adaptive_thinking',
        thinking_effort='high',
        fallback_group='claude-4.6',
    )
    err = RuntimeError(
        '`temperature` may only be set to 1 when thinking is enabled or in adaptive mode. '
        'No fallback model group found for original model_group=claude-sonnet-4-6.'
    )
    classified = _classify_llm_error(cfg, err)

    assert isinstance(classified, LLMConfigurationError)
    assert classified.category == 'model_configuration_error'
    assert 'temperature' in str(classified)
    assert 'fallback_group' in str(classified)


def test_classify_blank_content_block_error_with_fallback_hint():
    cfg = LLMConfig(
        provider='openai',
        model='claude-sonnet-4-6',
        api_key='dummy',
        base_url='https://proxy.example.com',
        model_family='claude-4.6',
        reasoning_protocol='anthropic_adaptive_thinking',
        thinking_effort='high',
        fallback_group='claude-sonnet-4-6',
    )
    err = RuntimeError(
        'The text field in the ContentBlock object at messages.3.content.48 is blank. '
        'Add text to the text field, and try again. '
        'No fallback model group found for original model_group=claude-sonnet-4-6.'
    )

    classified = _classify_llm_error(cfg, err)

    assert isinstance(classified, LLMConfigurationError)
    assert classified.category == 'model_configuration_error'
    assert '空' in str(classified)
    assert 'fallback_group=claude-sonnet-4-6' in str(classified)
