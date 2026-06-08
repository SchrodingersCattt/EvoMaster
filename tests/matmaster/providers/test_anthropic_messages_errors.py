from __future__ import annotations

from unittest.mock import MagicMock

import anthropic

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.errors import LLMError


def _provider() -> AnthropicMessagesTransport:
    return AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")


def _bad_request(message: str) -> anthropic.BadRequestError:
    return anthropic.BadRequestError(
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
    err = _provider().classify_error(anthropic.APITimeoutError(request=MagicMock()))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "timeout"


def test_auth_is_non_retryable() -> None:
    err = _provider().classify_error(
        anthropic.AuthenticationError(
            message="invalid key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "auth"


def test_context_overflow_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("context window exceeds token limit"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "context_overflow"


def test_prompt_too_long_bad_request_is_context_overflow() -> None:
    err = _provider().classify_error(
        _bad_request("prompt is too long: 201543 tokens > 200000 maximum")
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "context_overflow"


def test_signature_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("thinking signature is invalid"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "bad_request"


def test_generic_bad_request_is_retryable() -> None:
    err = _provider().classify_error(
        _bad_request("temporary invalid request from gateway")
    )
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "bad_request"
