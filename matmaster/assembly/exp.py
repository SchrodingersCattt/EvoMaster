"""Exp base class -- assembly layer abstraction.

Exp.assemble() consumes PlaygroundContext and outputs AgentRuntimeSpec.
Exp.run() provides the default flow: assemble -> AgentKernel.run -> return.
Subclasses (DirectExp, PlannerExp) override assemble() for different strategies.
Advanced subclasses can override run() for multi-step state machines.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from matmaster.types.context import PlaygroundContext
from matmaster.types.events import FinishEvent
from matmaster.types.runtime import AgentRuntimeSpec

if TYPE_CHECKING:
    from matmaster.engine.agent import AgentKernel


class Exp(ABC):
    """Abstract Exp base class -- assembly layer abstraction.

    Subclasses must implement assemble() which transforms PlaygroundContext
    into AgentRuntimeSpec. The default run() flow calls assemble() then
    passes the spec to AgentKernel.run().

    Naming convention: DirectExp.exp_name -> "Direct", PlannerExp -> "Planner".
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def exp_name(self) -> str:
        """Return the Exp name by stripping 'Exp' suffix from class name."""
        name = self.__class__.__name__
        return name[:-3] if name.endswith("Exp") else name

    @abstractmethod
    def assemble(self, ctx: PlaygroundContext, **kwargs: Any) -> AgentRuntimeSpec:
        """Transform PlaygroundContext into AgentRuntimeSpec.

        Subclasses implement this to build the complete runtime specification
        including tools, guards, hooks, system prompt, and LLM provider.
        """
        ...

    def run(
        self,
        ctx: PlaygroundContext,
        task: str,
        *,
        stop_event: threading.Event | None = None,
        **assemble_kwargs: Any,
    ) -> FinishEvent:
        """Default run flow: assemble -> AgentKernel.run -> return FinishEvent.

        Calls assemble() with ctx and any extra kwargs, creates an AgentKernel,
        and executes the kernel with the assembled spec and task string.
        """
        spec = self.assemble(ctx, **assemble_kwargs)
        from matmaster.engine.agent import AgentKernel  # lazy import to avoid circular

        kernel = AgentKernel()
        return kernel.run(spec, task, stop_event=stop_event)
