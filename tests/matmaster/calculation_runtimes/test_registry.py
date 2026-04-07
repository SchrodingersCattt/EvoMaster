from __future__ import annotations

from typing import cast

from matmaster.calculation_runtimes.base import CalculationRuntime
from matmaster.calculation_runtimes.registry import get_runtime_factory, register_runtime


class _FakeRuntime:
    def build_env(self) -> dict[str, str]:
        return {"A": "1"}

    def execution(self):
        return "execution"

    def build_submission(self, request):
        return request

    def materialize_input_path(self, *args, **kwargs):
        return "https://example.invalid/input"


def test_register_and_resolve_runtime_factory() -> None:
    register_runtime("fake", lambda session=None: cast(CalculationRuntime, _FakeRuntime()))

    factory = get_runtime_factory("fake")
    runtime = factory(None)

    assert runtime.build_env() == {"A": "1"}
