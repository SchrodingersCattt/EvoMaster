"""LLM 请求规范化、错误分类与流式 delta 解析（内部辅助函数）。"""

from __future__ import annotations

from typing import Any, Callable

from .config_models import (
    MODEL_FAMILY_DEFAULTS,
    LLMConfig,
    LLMConfigurationError,
    ModelProfile,
)


def _is_azure_base_url(base_url: str | None) -> bool:
    """判断 base_url 是否为 Azure OpenAI 端点"""
    if not base_url:
        return False
    return 'openai.azure.com' in base_url


def _azure_deployment_name(model: str) -> str:
    """从配置的 model 中取出 Azure 部署名（去掉 azure/ 前缀）"""
    s = (model or '').strip()
    if s.startswith('azure/'):
        return s[6:].strip() or model
    return s


def _build_anthropic_adaptive_thinking_request(effort: str) -> dict[str, Any]:
    return {
        'extra_body': {
            'thinking': {'type': 'adaptive'},
            'output_config': {'effort': effort},
        }
    }


def _build_openai_reasoning_effort_request(effort: str) -> dict[str, Any]:
    return {'reasoning_effort': effort}


_REASONING_PROTOCOL_BUILDERS: dict[str, Callable[[str], dict[str, Any]]] = {
    'anthropic_adaptive_thinking': _build_anthropic_adaptive_thinking_request,
    'openai_reasoning_effort': _build_openai_reasoning_effort_request,
}


def _infer_model_family_from_model(model: str) -> str | None:
    model_name = (model or '').strip().lower()
    if 'claude-sonnet-4-6' in model_name or 'claude-opus-4-6' in model_name:
        return 'claude-4.6'
    if 'gpt-5' in model_name:
        return 'gpt-5'
    if 'deepseek-reasoner' in model_name:
        return 'deepseek-reasoner'
    if 'gemini-3-flash-preview' in model_name:
        return 'gemini-3-flash-preview'
    return None


def _infer_reasoning_protocol_from_model(model: str) -> str | None:
    family = _infer_model_family_from_model(model)
    family_defaults = MODEL_FAMILY_DEFAULTS.get(family or '', {})
    if family_defaults.get('reasoning_protocol'):
        return family_defaults['reasoning_protocol']
    if (model or '').strip():
        return 'openai_reasoning_effort'
    return None


def _resolve_model_profile(config: LLMConfig) -> ModelProfile:
    family = config.model_family or _infer_model_family_from_model(config.model)
    defaults = MODEL_FAMILY_DEFAULTS.get(family or '', {})
    return ModelProfile(
        family=family,
        reasoning_protocol=(
            config.reasoning_protocol
            or defaults.get('reasoning_protocol')
            or _infer_reasoning_protocol_from_model(config.model)
        ),
        fallback_group=config.fallback_group or defaults.get('fallback_group'),
        temperature_policy=(
            config.temperature_policy or defaults.get('temperature_policy') or 'default'
        ),
    )


def _resolve_reasoning_protocol(config: LLMConfig) -> str | None:
    return _resolve_model_profile(config).reasoning_protocol


def _build_reasoning_request_overrides(config: LLMConfig) -> dict[str, Any]:
    """构造 reasoning/thinking 相关的 provider 请求覆盖参数。"""
    effort = (config.thinking_effort or '').strip().lower()
    if not effort:
        return {}

    protocol = _resolve_reasoning_protocol(config)
    if not protocol:
        return {}

    builder = _REASONING_PROTOCOL_BUILDERS.get(protocol)
    if builder is None:
        raise ValueError(f'Unsupported reasoning protocol: {protocol}')
    return builder(effort)


def _normalize_request_params(
    config: LLMConfig, request_params: dict[str, Any]
) -> dict[str, Any]:
    """按统一模型画像规范化请求参数，优先在本地消化 provider 特殊约束。"""
    normalized = request_params.copy()
    profile = _resolve_model_profile(config)
    thinking_enabled = bool((config.thinking_effort or '').strip())
    if (
        thinking_enabled
        and profile.reasoning_protocol == 'anthropic_adaptive_thinking'
        and profile.temperature_policy == 'force_one_when_reasoning'
    ):
        normalized['temperature'] = 1
    return normalized


def _classify_llm_error(
    config: LLMConfig, error: Exception
) -> LLMConfigurationError | None:
    """将长 provider 错误归一为高信号、不可重试的配置错误。"""
    err_text = str(error)
    err_lower = err_text.lower()
    issues: list[str] = []
    profile = _resolve_model_profile(config)

    if (
        'temperature' in err_lower
        and 'may only be set to 1' in err_lower
        and profile.reasoning_protocol == 'anthropic_adaptive_thinking'
    ):
        issues.append(
            '当前模型在 thinking/adaptive 模式下 temperature 必须为 1；'
            '请通过统一模型策略规范化温度，避免把其他采样值直接发给上游。'
        )

    if 'contentblock object' in err_lower and 'is blank' in err_lower:
        issues.append(
            '回放的 assistant 历史中包含空的 text/thinking 内容块；'
            '请在发送前过滤空 content 与空 reasoning block，避免 Bedrock/Anthropic 拒绝该消息。'
        )

    if 'no fallback model group found' in err_lower:
        group = profile.fallback_group
        if group:
            issues.append(
                f'上游代理未识别 fallback_group={group}；'
                '请检查代理侧 fallback 分组是否与本地模型画像一致。'
            )
        else:
            issues.append(
                '当前模型未声明 fallback_group；请在统一模型配置中补齐该字段。'
            )

    if not issues:
        return None

    model_label = profile.family or config.model
    return LLMConfigurationError(
        category='model_configuration_error',
        message=f"LLM 配置错误（{model_label}）：{' '.join(issues)}",
        raw_error=err_text,
    )


def _merge_request_overrides(
    request_params: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """合并 reasoning 请求覆盖，保留已有 extra_body 字段。"""
    if not overrides:
        return request_params

    merged = request_params.copy()
    for key, value in overrides.items():
        if (
            key == 'extra_body'
            and isinstance(value, dict)
            and isinstance(merged.get('extra_body'), dict)
        ):
            merged['extra_body'] = {
                **merged['extra_body'],
                **value,
            }
        else:
            merged[key] = value
    return merged


def _merge_api_message_extras(
    current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """递归合并 assistant 原始扩展字段，用于多轮回放。"""
    if not incoming:
        return current

    merged = dict(current)
    for key, value in incoming.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_api_message_extras(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            merged[key] = [*existing, *value]
        else:
            merged[key] = value
    return merged


def _extract_reasoning_delta(delta: Any) -> str | None:
    """从流式 chunk delta 中提取 reasoning/thinking 内容。

    按优先级依次检查：
    1. delta.reasoning_content — OpenAI 原生格式（o1/o3 系列）
    2. delta.reasoning — 部分 LiteLLM 版本的别名
    3. model_extra 中的 thinking_blocks / thinking_delta — LiteLLM 代理 Claude 时的格式
    """
    # OpenAI 原生
    rc = getattr(delta, 'reasoning_content', None)
    if rc:
        return rc
    # 部分 proxy 别名
    rc = getattr(delta, 'reasoning', None)
    if rc:
        return rc
    # LiteLLM Claude 代理：thinking 内容可能在 model_extra
    extras = getattr(delta, 'model_extra', None) or {}
    # provider_specific_fields.thinking_blocks[].thinking
    psf = extras.get('provider_specific_fields') or {}
    thinking_blocks = psf.get('thinking_blocks') or []
    if thinking_blocks:
        parts = [
            b.get('thinking', '') if isinstance(b, dict) else ''
            for b in thinking_blocks
        ]
        text = ''.join(parts)
        if text:
            return text
    # thinking_delta 字段（某些 LiteLLM 版本的流式增量格式）
    td = psf.get('thinking_delta') or extras.get('thinking_delta')
    if td:
        return td if isinstance(td, str) else str(td)
    return None
