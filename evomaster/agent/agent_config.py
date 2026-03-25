"""Agent 配置模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .context import ContextConfig


class AgentConfig(BaseModel):
    """Agent 配置"""

    max_turns: int = Field(default=100, description='最大执行轮数')
    context_config: ContextConfig = Field(
        default_factory=ContextConfig, description='上下文管理配置'
    )
