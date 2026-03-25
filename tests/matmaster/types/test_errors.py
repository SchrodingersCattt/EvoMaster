"""Tests for LLMError custom exception."""

from matmaster.types.errors import LLMError


class TestLLMError:
    def test_retryable_default_true(self) -> None:
        err = LLMError("timeout")
        assert err.retryable is True
        assert str(err) == "timeout"

    def test_retryable_false(self) -> None:
        err = LLMError("auth failed", retryable=False)
        assert err.retryable is False

    def test_exception_chaining(self) -> None:
        original = ValueError("original")
        err = LLMError("wrapped", retryable=True)
        err.__cause__ = original
        assert err.__cause__ is original
