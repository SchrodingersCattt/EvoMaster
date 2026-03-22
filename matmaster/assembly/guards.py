"""Business guard implementations for Exp layer injection.

Guard shells (ManuscriptGateGuard, AuthFailureGateGuard) removed in Phase 6
per D-13/D-14. Guard injection mechanism from Phase 2-3 remains available
via DirectExp(guards=[...]) -> GuardPipeline.

Future business guards should be implemented as Hooks, not Guards.
"""
