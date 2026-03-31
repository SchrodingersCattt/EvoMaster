"""Custom exceptions for the matmaster LLM layer."""

from __future__ import annotations

from typing import Any


class LLMError(Exception):
    """LLM call exception. retryable indicates whether caller should retry."""

    def __init__(  # noqa: B042
        self,
        message: str,
        *,
        retryable: bool = True,
        error_category: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_category = error_category
        self.attempts = attempts
