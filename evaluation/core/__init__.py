"""MATTER v5 evaluation engine (question bank → run → score → report)."""

from .runner import run_evaluation
from .schemas import EvalConfig

__all__ = ["EvalConfig", "run_evaluation"]
