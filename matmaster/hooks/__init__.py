"""Business hooks for the matmaster agent kernel.

ConfirmationHook is the only remaining business hook. EventEmitterHook
was retired to core/hooks.py BaseHook. AssistantStateHook, SkillHitHook,
and OutputProcessorHook were retired in Phase 34 (generator events replace
their functionality).
"""

from matmaster.hooks.confirmation import ConfirmationHook

__all__ = [
    "ConfirmationHook",
]
