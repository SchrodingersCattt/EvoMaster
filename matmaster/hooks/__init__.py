"""Business hooks for the matmaster agent kernel.

All business hooks have been retired:
- AssistantStateHook, SkillHitHook, OutputProcessorHook: retired Phase 34
  (generator events replace their functionality)
- ConfirmationHook: retired Phase 36 (D-03/D-04, not rebuilt until v2.3+
  generator bidirectional stream work)

Hook infrastructure (Hook Protocol / BaseHook / HookAction) lives in
matmaster/core/hooks.py and is still used by DevStreamHook and kernel.
"""

__all__: list[str] = []
