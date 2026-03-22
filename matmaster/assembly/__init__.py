"""matmaster.assembly -- Exp assembly layer.

Provides the Exp base class, DirectExp implementation, ToolRegistry,
ContextBuilder, business guards, and WorkerRegistry Protocol for
assembling AgentRuntimeSpec from PlaygroundContext.
"""

from .context_builder import ContextBuilder
from .direct_exp import DirectExp
from .exp import Exp
from .guards import AuthFailureGateGuard, ManuscriptGateGuard
from .tool_registry import Tool, ToolRegistry
from .worker_registry import WorkerRegistry

__all__ = [
    "AuthFailureGateGuard",
    "ContextBuilder",
    "DirectExp",
    "Exp",
    "ManuscriptGateGuard",
    "Tool",
    "ToolRegistry",
    "WorkerRegistry",
]
