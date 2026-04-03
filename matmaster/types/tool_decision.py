"""ToolDecision -- result type for capability policy evaluation.

Returned by CapabilityPolicy.evaluate() to indicate whether a tool call
should be allowed or denied, with optional reason and guidance for the LLM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ToolDecision(BaseModel):
    """Decision from capability policy evaluation.

    decision="allow" lets the tool call proceed.
    decision="deny" blocks the call; reason explains why, guidance is
    injected into the LLM prompt to steer away from the blocked pattern.
    """

    decision: Literal["allow", "deny"]
    reason: str | None = None
    guidance: str | None = None
