"""matmaster.core -- Runtime core components.

Note: Exp is lazy-imported via __getattr__ to avoid circular import
(core.exp -> types.runtime -> core.hooks -> core.__init__).
"""

from .agent import AgentKernel
from .context_builder import ContextBuilder
from .hooks import HookEvent, HookExecutor, HookOutcome, HookResult
from .playground import Playground, PlaygroundManager

__all__ = [
    "AgentKernel",
    "ContextBuilder",
    "Exp",
    "HookEvent",
    "HookExecutor",
    "HookOutcome",
    "HookResult",
    "Playground",
    "PlaygroundManager",
]


def __getattr__(name: str):
    if name == "Exp":
        from .exp import Exp

        return Exp
    if name == "exp":
        import importlib

        return importlib.import_module("matmaster.core.exp")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
