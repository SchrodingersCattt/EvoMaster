"""Custom exceptions for the matmaster LLM layer."""

from __future__ import annotations


class LLMError(Exception):
    """LLM call exception. retryable indicates whether caller should retry."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
