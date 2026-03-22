"""Business guard shells for Exp layer injection.

These guards implement the Guard Protocol (matmaster/types/guards.py)
and are injected into AgentRuntimeSpec.guards via Exp.assemble().

Phase 3 provides shell implementations that always allow.
Phase 5 migrates actual business logic from ToolGuard.
"""

import logging

from matmaster.types.guards import GuardContext, GuardResult

logger = logging.getLogger(__name__)


class ManuscriptGateGuard:
    """Manuscript completion gate guard.

    Blocks finish tool call when manuscript sections are not validated.
    Shell implementation: always allows (Phase 5 adds real logic from
    ToolGuard.can_finish_manuscript).
    """

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class AuthFailureGateGuard:
    """Authentication failure gate guard.

    Blocks further tool calls after consecutive auth failures.
    Shell implementation: always allows (Phase 5 adds real logic from
    ToolGuard.auth_failure_gate).
    """

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)
