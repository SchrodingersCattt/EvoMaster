"""Compatibility shim for the Phase 3 system prompt move.

The real implementation lives in `matmaster.context.system_prompt`.
This shim stays until Phase 4 renames AgentRuntimeSpec.context_builder to
system_prompt_builder and removes legacy import paths.
"""

from matmaster.context.system_prompt import ContextBuilder  # noqa: F401
