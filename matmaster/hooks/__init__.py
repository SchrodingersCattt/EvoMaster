"""Business hooks for the matmaster agent kernel.

These hooks inject business logic into the kernel execution loop per D-07/D-08.
EventEmitterHook (the generic kernel->bus bridge) stays in core/hooks.py.

Each hook inherits BaseHook and overrides only the relevant hook points.
"""

from matmaster.hooks.assistant_state import AssistantStateHook
from matmaster.hooks.confirmation import ConfirmationHook
from matmaster.hooks.output_processor import OutputProcessorHook
from matmaster.hooks.skill_hit import SkillHitHook

__all__ = [
    "AssistantStateHook",
    "ConfirmationHook",
    "OutputProcessorHook",
    "SkillHitHook",
]
