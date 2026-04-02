"""Tool Runtime v2 decision type -- ToolDecision.

ToolDecision is the result of a constraint evaluation (StructuralValidation,
RunStateGuard, CapabilityPolicy). It replaces the old GuardResult for tool
runtime purposes while GuardResult continues to serve the kernel-level
GuardPipeline.

Frozen Pydantic model: immutable after construction.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolDecision(BaseModel):
    """Result of a tool constraint evaluation.

    - allow: tool execution proceeds
    - deny: tool execution blocked, reason explains why,
      guidance is optionally injected into the LLM prompt
    """

    model_config = ConfigDict(frozen=True)

    decision: Literal["allow", "deny"]
    reason: str = ""
    guidance: str | None = None
