"""matmaster.core -- Runtime core components.

Provides the agent kernel execution loop, exp assembly layer,
playground environment preparation, event bus, and supporting
infrastructure (guards, hooks, context builder).

Note: Exp and DirectExp are lazy-imported via __getattr__ to avoid
circular import (core.exp -> core.agent) triggered during types.runtime
loading of ToolRegistry.
"""

from .agent import AgentKernel
from .bus import MessageBus
from .context_builder import ContextBuilder
from .guard_pipeline import GuardPipeline, LoopDetectionGuard
from .hooks import BaseHook, EventEmitterHook, Hook, HookAction
from .playground import Playground

__all__ = [
    "AgentKernel",
    "BaseHook",
    "ContextBuilder",
    "DirectExp",
    "EventEmitterHook",
    "Exp",
    "GuardPipeline",
    "Hook",
    "HookAction",
    "LoopDetectionGuard",
    "MessageBus",
    "Playground",
]


def __getattr__(name: str):
    if name == "Exp":
        from .exp import Exp

        return Exp
    if name == "DirectExp":
        from .direct_exp import DirectExp

        return DirectExp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
