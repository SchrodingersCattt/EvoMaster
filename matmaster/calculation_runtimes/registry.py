from __future__ import annotations

from collections.abc import Callable

from .base import CalculationRuntime

_RUNTIME_FACTORIES: dict[str, Callable[[object | None], CalculationRuntime]] = {}


def register_runtime(
    name: str, factory: Callable[[object | None], CalculationRuntime]
) -> None:
    _RUNTIME_FACTORIES[name] = factory


def get_runtime_factory(name: str) -> Callable[[object | None], CalculationRuntime]:
    try:
        return _RUNTIME_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown calculation runtime: {name!r}") from exc
