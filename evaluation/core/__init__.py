"""MATTER v5 evaluation engine (question bank → run → score → report)."""

from .schemas import EvalConfig

__all__ = ['EvalConfig', 'run_evaluation']


def __getattr__(name: str):
    if name == 'run_evaluation':
        from .runner import run_evaluation as _run_evaluation

        return _run_evaluation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
