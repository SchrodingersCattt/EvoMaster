"""LLM 工厂函数。"""

from __future__ import annotations

from typing import Any

from .anthropic_llm import AnthropicLLM
from .base import BaseLLM
from .config_models import LLMConfig
from .deepseek_llm import DeepSeekLLM
from .openai_llm import OpenAILLM


def create_llm(
    config: LLMConfig, output_config: dict[str, Any] | None = None
) -> BaseLLM:
    """LLM 工厂函数

    Args:
        config: LLM 配置
        output_config: 输出显示配置

    Returns:
        LLM 实例

    Raises:
        ValueError: 不支持的提供商
    """
    if config.provider == 'openai' or config.provider == 'openrouter':
        return OpenAILLM(config, output_config=output_config)
    elif config.provider == 'anthropic':
        return AnthropicLLM(config, output_config=output_config)
    elif config.provider == 'deepseek':
        return DeepSeekLLM(config, output_config=output_config)
    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
