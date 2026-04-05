"""Tool Runtime v2 decision type -- ToolDecision.

ToolDecision is the result of a constraint evaluation
(StructuralValidation, CapabilityPolicy) within the tool runtime pipeline.

Frozen Pydantic model: immutable after construction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ToolDecision(BaseModel):
    """Result of a tool constraint evaluation.

    - allow: tool execution proceeds
    - deny: tool execution blocked, reason explains why,
      guidance is optionally injected into the LLM prompt
    - modified_args: if Layer A normalizes arguments (e.g., path resolution),
      the updated args dict is returned here; Runner uses these instead of
      the original tool_args.
    """

    model_config = ConfigDict(frozen=True)

    decision: Literal["allow", "deny"]
    reason: str = ""
    guidance: str | None = None
    modified_args: dict[str, Any] | None = None
