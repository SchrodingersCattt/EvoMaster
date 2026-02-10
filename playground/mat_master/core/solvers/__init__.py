"""Mat Master Mode layer: how to work (direct vs plan-execute).

- DirectSolver: on-the-fly execution, routes to capabilities (SkillEvolution or WorkerExp).
- ResearchPlanner: plan-first execution, generates plan then runs each step via DirectSolver.

Note: Resilient calculation (submit/monitor/diagnose/retry) is now a skill (job-manager),
not a separate routing category. Both modes use it naturally via use_skill.
"""

from .direct_solver import DirectSolver
from .research_planner import ResearchPlanner

__all__ = ["DirectSolver", "ResearchPlanner"]
