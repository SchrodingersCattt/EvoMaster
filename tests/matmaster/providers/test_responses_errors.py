from __future__ import annotations

from unittest.mock import MagicMock

import openai

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.errors import LLMError


def _provider() -> ResponsesTransport:
    return ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")


def _bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        message=message,
        response=MagicMock(status_code=400, headers={}),
        body=None,
    )


def test_existing_llm_error_is_not_rewrapped() -> None:
    assert (
        _provider().classify_error(
            LLMError("x", retryable=False, error_category="bad_request")
        )
        is None
    )


def test_timeout_is_retryable() -> None:
    err = _provider().classify_error(openai.APITimeoutError(request=MagicMock()))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "timeout"


def test_rate_limit_is_retryable() -> None:
    err = _provider().classify_error(
        openai.RateLimitError(
            message="slow down",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
    )
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "rate_limit"


def test_auth_is_non_retryable() -> None:
    err = _provider().classify_error(
        openai.AuthenticationError(
            message="bad key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "auth"


def test_context_overflow_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("context length exceeds token limit"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "context_overflow"


def test_reasoning_replay_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(
        _bad_request("reasoning item rs_1 without its required following item")
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "bad_request"


def test_generic_bad_request_is_retryable() -> None:
    err = _provider().classify_error(_bad_request("temporary gateway hiccup"))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "bad_request"
