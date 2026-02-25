"""Mat Master Mode layer: how to work (direct vs plan-execute).

- DirectSolver: on-the-fly execution, routes to capabilities (SkillEvolution or WorkerExp).
- ResearchPlanner: plan-first execution, generates plan then runs each step via DirectSolver.

Note: Resilient calculation (submit/monitor/diagnose/retry) is now handled by the
monitor_job built-in tool, not a separate routing category or skill.
"""

from .direct_solver import DirectSolver
from .research_planner import ResearchPlanner

__all__ = ["DirectSolver", "ResearchPlanner"]
