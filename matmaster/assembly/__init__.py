"""matmaster.assembly -- Exp assembly layer.

Provides the Exp base class, DirectExp implementation, ToolRegistry,
ContextBuilder, business guards, and WorkerRegistry Protocol for
assembling AgentRuntimeSpec from PlaygroundContext.

Note: Exp and DirectExp are lazy-imported via __getattr__ to avoid a
circular import chain (assembly -> exp -> engine.agent) that would be
triggered during types.runtime loading of ToolRegistry.
"""

from .context_builder import ContextBuilder
from .evomaster_tool_adapter import EvoToolAdapter
from .guards import AuthFailureGateGuard, ManuscriptGateGuard
from .tool_registry import Tool, ToolRegistry
from .worker_registry import WorkerRegistry

__all__ = [
    "AuthFailureGateGuard",
    "ContextBuilder",
    "DirectExp",
    "EvoToolAdapter",
    "Exp",
    "ManuscriptGateGuard",
    "Tool",
    "ToolRegistry",
    "WorkerRegistry",
]


def __getattr__(name: str):
    if name == "Exp":
        from .exp import Exp

        return Exp
    if name == "DirectExp":
        from .direct_exp import DirectExp

        return DirectExp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
