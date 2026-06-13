"""openai SDK 系 transport 的共享件：client 生命周期 + SDK 异常归类。

bad_request 的非重试判定按 wire 协议各异，由子类经 _is_non_retryable_bad_request
钩子注入；其余异常阶梯（timeout/connection/rate_limit/server/auth/context）一致。
"""

from __future__ import annotations

import openai

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError


class OpenAISDKTransport(Transport):
    _api_key: str
    _base_url: str | None

    async def _open_client(self) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=self._build_http_client(),
        )

    async def _close_client(self, client: openai.AsyncOpenAI) -> None:
        await client.close()

    def _is_non_retryable_bad_request(self, err_str: str) -> bool:
        raise NotImplementedError

    def classify_error(self, exc: Exception) -> LLMError | None:
        import httpx as _httpx

        if isinstance(exc, LLMError):
            return None
        if isinstance(exc, openai.APITimeoutError):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(str(exc), retryable=True, error_category="connection")
        if isinstance(exc, openai.RateLimitError):
            return LLMError(str(exc), retryable=True, error_category="rate_limit")
        if isinstance(exc, openai.InternalServerError):
            return LLMError(str(exc), retryable=True, error_category="server")
        if isinstance(exc, _httpx.ReadTimeout):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMError(str(exc), retryable=False, error_category="auth")
        if isinstance(exc, openai.BadRequestError):
            err_str = str(exc)
            err_text = err_str.lower()
            if "context" in err_text and ("length" in err_text or "token" in err_text):
                return LLMError(
                    err_str, retryable=False, error_category="context_overflow"
                )
            if self._is_non_retryable_bad_request(err_str):
                return LLMError(err_str, retryable=False, error_category="bad_request")
            return LLMError(err_str, retryable=True, error_category="bad_request")
        return None
