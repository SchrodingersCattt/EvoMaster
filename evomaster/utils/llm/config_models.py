"""LLM 配置与响应模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from evomaster.utils.types import AssistantMessage, ToolCall


class LLMConfig(BaseModel):
    """LLM 配置"""

    provider: Literal['openai', 'anthropic', 'deepseek', 'openrouter'] = Field(
        description='LLM 提供商'
    )
    model: str = Field(
        description='模型名称（OpenAI 为 model id；Azure 为部署名，可写 azure/部署名）'
    )
    api_key: str = Field(description='API Key，必须在配置中提供')
    base_url: str | None = Field(default=None, description='API Base URL')
    api_version: str | None = Field(
        default=None, description='Azure 专用：API 版本，如 2024-06-01'
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description='采样温度')
    max_tokens: int | None = Field(default=None, description='最大生成 token 数')
    timeout: int = Field(
        default=300, description='非流式请求超时时间（秒）；大模型长输出需要 2-3 分钟'
    )
    stream_timeout: int | None = Field(
        default=None,
        description=(
            '流式请求首 token 超时时间（秒）。None 表示回退到 timeout。'
            '控制的是：发起流式请求后，最多等多久才能收到第一个 chunk。'
            '建议公有云 API 设 20s，内网 proxy 设 30s，本地 sglang/vLLM 设 60s。'
        ),
    )
    stream_idle_timeout: int = Field(
        default=60,
        description=(
            '流式传输过程中，相邻两次 chunk 之间允许的最大空闲时间（秒）。'
            '一旦开始流式输出，若超过此时间没有新数据则判定超时。'
            '公网网关/长上下文下建议 60~120s；与首 token 超时（stream_timeout）无关。'
        ),
    )
    max_retries: int = Field(default=3, description='最大重试次数')
    retry_delay: float = Field(default=1.0, description='重试延迟（秒）')
    use_completion_api: bool = Field(
        default=False, description='使用 Completion API 而非 Chat API'
    )
    thinking_effort: str | None = Field(
        default=None,
        description='统一的 reasoning/thinking 强度配置，供 provider 适配层使用',
    )
    reasoning_protocol: (
        Literal['anthropic_adaptive_thinking', 'openai_reasoning_effort'] | None
    ) = Field(
        default=None,
        description='显式指定 reasoning 请求协议；为空时回退到兼容推断逻辑',
    )
    model_family: str | None = Field(
        default=None,
        description='统一模型族标识，用于能力约束、fallback 分组和请求规范化',
    )
    fallback_group: str | None = Field(
        default=None,
        description='上游代理使用的 fallback 分组名；为空时可由模型族默认值补齐',
    )
    temperature_policy: Literal['default', 'force_one_when_reasoning'] | None = Field(
        default=None,
        description='温度规范化策略；支持按模型族自动收敛 provider 特殊约束',
    )


class LLMResponse(BaseModel):
    """LLM 响应"""

    content: str | None = Field(default=None, description='生成的文本内容')
    reasoning_content: str | None = Field(
        default=None, description='模型返回的推理内容'
    )
    api_message_extras: dict[str, Any] = Field(
        default_factory=dict,
        description='需要在后续 assistant message 中保留的原始扩展字段',
    )
    tool_calls: list[ToolCall] | None = Field(default=None, description='工具调用列表')
    finish_reason: str | None = Field(default=None, description='结束原因')
    usage: dict[str, int] = Field(default_factory=dict, description='Token 使用统计')
    meta: dict[str, Any] = Field(default_factory=dict, description='其他元数据')

    def to_assistant_message(self) -> AssistantMessage:
        """转换为 AssistantMessage"""
        return AssistantMessage(
            content=self.content,
            tool_calls=self.tool_calls,
            meta={
                **self.meta,
                'finish_reason': self.finish_reason,
                'usage': self.usage,
                'reasoning_content': self.reasoning_content,
                'api_message_extras': self.api_message_extras,
            },
        )


class LLMConfigurationError(RuntimeError):
    """LLM 配置/能力约束错误，适合直接反馈给配置维护者。"""

    def __init__(
        self,
        *,
        category: str,
        message: str,
        raw_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.raw_error = raw_error


@dataclass(frozen=True)
class ModelProfile:
    family: str | None
    reasoning_protocol: str | None
    fallback_group: str | None
    temperature_policy: str


MODEL_FAMILY_DEFAULTS: dict[str, dict[str, str]] = {
    'claude-4.6': {
        'reasoning_protocol': 'anthropic_adaptive_thinking',
        'fallback_group': 'claude-4.6',
        'temperature_policy': 'force_one_when_reasoning',
    },
    'gpt-5': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'fallback_group': 'gpt-5',
        'temperature_policy': 'default',
    },
    'deepseek-reasoner': {
        'reasoning_protocol': 'openai_reasoning_effort',
        'fallback_group': 'deepseek-reasoner',
        'temperature_policy': 'default',
    },
    'gemini-3-flash-preview': {
        'fallback_group': 'gemini-3-flash-preview',
        'temperature_policy': 'default',
    },
}
