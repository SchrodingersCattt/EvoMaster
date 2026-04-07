from __future__ import annotations

from typing import Any, Protocol

from .types import ExecutionContextLike, SubmissionRequest, SubmissionSpecLike


class CalculationRuntime(Protocol):
    def build_env(self) -> dict[str, str]: ...

    def execution(self) -> ExecutionContextLike: ...

    def build_submission(self, request: SubmissionRequest) -> SubmissionSpecLike: ...

    def materialize_input_path(self, *args: Any, **kwargs: Any) -> str: ...
