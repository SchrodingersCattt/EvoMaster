"""Sub-agent isolation layer for planner step execution.

Each plan step is executed as an isolated "sub-agent" — the shared agent's
dialog context is reset before every step so that accumulated conversation
from previous steps cannot leak into the current one.  This prevents the
overstepping problem where the solver sees the overall goal and attempts
to complete future steps.

Design constraints (from architecture doc):
  - Context isolation is the primary overstepping prevention mechanism.
  - Hub-and-spoke: sub-agents only report to the planner, never to each other.
  - Concurrency reserved but currently set to 1.
  - Memory tools (``mem_save``, ``mem_recall``) are excluded from planner
    sub-agents to prevent cross-session memory leaking future-step info.
"""

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from evomaster.utils.types import TaskInstance

from .direct_solver import DirectSolver

logger = logging.getLogger('MatMaster.StepSubAgent')

# Tools excluded from planner sub-agents to prevent cross-session memory
# leaking future-step information into the current step's context.
_DEFAULT_EXCLUDED_TOOLS: frozenset[str] = frozenset({'mem_save', 'mem_recall'})


class SubAgentHandle:
    """Lightweight handle wrapping a single step execution with context isolation.

    Lifecycle:
      1. ``prepare()`` — resets agent context to a clean slate.
      2. ``run()``     — executes the step via a fresh ``DirectSolver``.
      3. Result is returned to the planner; the handle is discarded.

    The handle does NOT create a new agent instance.  It reuses the existing
    agent but calls ``reset_context()`` to wipe accumulated dialog, achieving
    the same isolation as a fresh agent at near-zero cost.
    """

    def __init__(
        self,
        agent,
        config,
        *,
        step_id: int,
        step_turn_budget: int = 30,
        workspaces: Path | None = None,
        excluded_tools: frozenset[str] = _DEFAULT_EXCLUDED_TOOLS,
    ):
        self._agent = agent
        self._config = config
        self._step_id = step_id
        self._step_turn_budget = step_turn_budget
        self._workspaces = workspaces
        self._excluded_tools = excluded_tools
        self._solver: DirectSolver | None = None
        self._prepared = False

    # ── lifecycle ────────────────────────────────────────────────────────

    def prepare(self) -> None:
        """Reset agent context and create a fresh DirectSolver for this step.

        After ``prepare()``, the agent's dialog contains only the initial
        system prompt — no previous step history, no overall goal leakage.
        """
        # 1. Ensure _initial_system_prompt is set before reset_context().
        #    In the planner flow the agent is used for LLM queries (via
        #    _stream_llm) but those do NOT call agent._initialize(), so
        #    _initial_system_prompt may still be None when the first step
        #    tries to reset.  We bootstrap it here from _get_system_prompt().
        if getattr(self._agent, '_initial_system_prompt', None) is None:
            self._agent._initial_system_prompt = self._agent._get_system_prompt()
            logger.info(
                '[SubAgent] Bootstrapped _initial_system_prompt (was None)'
            )

        # 2. Reset the agent's dialog to initial system prompt only.
        self._agent.reset_context()

        # 2b. Filter out excluded tools (e.g. mem_save/mem_recall) from the
        #     dialog so the sub-agent cannot retrieve cross-session memories.
        if self._excluded_tools and hasattr(self._agent, 'current_dialog'):
            dialog = self._agent.current_dialog
            if dialog is not None and dialog.tools:
                original_count = len(dialog.tools)
                dialog.tools = [
                    spec
                    for spec in dialog.tools
                    if getattr(getattr(spec, 'function', None), 'name', '') not in self._excluded_tools
                ]
                removed = original_count - len(dialog.tools)
                if removed:
                    logger.info(
                        '[SubAgent] Filtered %d tool(s) from dialog: %s',
                        removed,
                        sorted(self._excluded_tools),
                    )

        # 3. Override the agent's per-run max_turns to the step budget so
        #    the sub-agent cannot consume the entire planner budget.
        original_max_turns = getattr(
            getattr(self._agent, 'config', None), 'max_turns', None
        )
        if original_max_turns is not None and self._step_turn_budget > 0:
            self._agent.config.max_turns = self._step_turn_budget

        # 4. Create a fresh DirectSolver (stateless — no accumulated results).
        self._solver = DirectSolver(self._agent, self._config)
        if self._workspaces is not None:
            self._solver.set_run_dir(self._workspaces)

        self._prepared = True
        logger.info(
            '[SubAgent] Step %s prepared: context reset, turn_budget=%d',
            self._step_id,
            self._step_turn_budget,
        )

    def run(
        self,
        step_prompt: str,
        task_id: str,
        *,
        task: Optional[TaskInstance] = None,
    ) -> dict[str, Any]:
        """Execute the step and return the solver result dict.

        Args:
            step_prompt: The isolated step prompt (no overall goal).
            task_id: Unique task identifier for this step.
            task: Optional pre-built TaskInstance (with dialog_history).

        Returns:
            Result dict from ``DirectSolver.run()``.
        """
        if not self._prepared:
            raise RuntimeError(
                f'SubAgentHandle for step {self._step_id} was not prepared. '
                'Call prepare() before run().'
            )
        assert self._solver is not None

        if task is not None:
            return self._solver.run(task=task)
        return self._solver.run(step_prompt, task_id=task_id)

    @property
    def solver(self) -> DirectSolver | None:
        """Access the underlying DirectSolver (for fallback execution)."""
        return self._solver


class StepSubAgentFactory:
    """Factory that produces isolated ``SubAgentHandle`` instances per step.

    Initialised once in ``ResearchPlanner.__init__`` and reused across all
    steps.  The factory holds references to the shared agent and config but
    does NOT mutate them — mutation happens inside ``SubAgentHandle.prepare()``.

    Config keys (under ``mat_master.planner.sub_agent``):
      - ``enabled``: bool — toggle sub-agent mode (default: ``true``).
      - ``step_turn_budget``: int — max turns per step (default: ``30``).
    """

    def __init__(
        self,
        agent,
        config,
        *,
        enabled: bool = True,
        step_turn_budget: int = 30,
        original_max_turns: int = 200,
        excluded_tools: Sequence[str] | None = None,
    ):
        self._agent = agent
        self._config = config
        self.enabled = enabled
        self._step_turn_budget = step_turn_budget
        self._original_max_turns = original_max_turns
        self._excluded_tools: frozenset[str] = (
            frozenset(excluded_tools) if excluded_tools is not None
            else _DEFAULT_EXCLUDED_TOOLS
        )
        logger.info(
            '[SubAgentFactory] Initialised: enabled=%s, step_turn_budget=%d, excluded_tools=%s',
            self.enabled,
            self._step_turn_budget,
            sorted(self._excluded_tools) if self._excluded_tools else '(none)',
        )

    def create(
        self,
        *,
        step_id: int,
        workspaces: Path | None = None,
    ) -> SubAgentHandle:
        """Create a new ``SubAgentHandle`` for the given step.

        The handle is NOT yet prepared — caller must invoke ``handle.prepare()``
        before ``handle.run()``.
        """
        return SubAgentHandle(
            self._agent,
            self._config,
            step_id=step_id,
            step_turn_budget=self._step_turn_budget,
            workspaces=workspaces,
            excluded_tools=self._excluded_tools,
        )

    def restore_agent_state(self) -> None:
        """Restore the agent's max_turns to the original value.

        Should be called after all steps are done (e.g. in ``run()`` cleanup)
        to avoid leaving the agent with a reduced turn budget.
        """
        agent_config = getattr(self._agent, 'config', None)
        if agent_config is not None and hasattr(agent_config, 'max_turns'):
            agent_config.max_turns = self._original_max_turns
            logger.debug(
                '[SubAgentFactory] Restored agent max_turns to %d',
                self._original_max_turns,
            )
