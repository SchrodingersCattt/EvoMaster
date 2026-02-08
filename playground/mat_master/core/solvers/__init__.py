"""Mat Master Mode layer: how to work (direct vs plan-execute).

- DirectSolver: on-the-fly execution, routes to capabilities (ResilientCalc, SkillEvolution) or WorkerExp.
- ResearchPlanner: plan-first execution, generates plan then runs each step via DirectSolver.
"""

from .direct_solver import DirectSolver
from .research_planner import ResearchPlanner

__all__ = ["DirectSolver", "ResearchPlanner"]
