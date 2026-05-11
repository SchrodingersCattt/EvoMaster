from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import requests
from tenacity import Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_random_exponential
from urllib3.util.retry import Retry

from .errors import NonRetryableTransferError, RetryableTransferError
from .security import redact_secrets


@dataclass(frozen=True)
class RetryPolicy:
    total_attempts: int = 5
    http_attempts: int = 3
    business_attempts: int = 2
    timeout_seconds: int = 300
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)


@dataclass(frozen=True)
class StoreHostResponse:
    data: dict[str, Any]
    headers: dict[str, str]
    status_code: int
    raw_body: dict[str, Any]


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableTransferError) and exc.retryable


def _request_body(data: Any) -> Any:
    if callable(data):
        return data()
    return data


def _safe_response_text(response: Any) -> str:
    return redact_secrets(str(getattr(response, "text", "") or ""))


def _message_from_body(body: dict[str, Any], fallback: str) -> str:
    message = body.get("message") or body.get("msg") or body.get("error") or fallback
    return redact_secrets(str(message))


def build_download_url(store_host: str, object_key: str, token: str) -> str:
    encoded_key = quote(object_key, safe="")
    query = urlencode(
        {
            "token": token,
            "Response-Content-Type": "application/octet-stream",
        },
        quote_via=quote,
        safe="",
    )
    return f"{store_host.rstrip('/')}/api/download/{encoded_key}?{query}"


def request_storehost_json(
    session,
    method: str,
    url: str,
    *,
    stage: str,
    expected_code: int | None = 0,
    retryable_business_codes: Iterable[int] | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    data: Any = None,
    timeout: int | None = None,
    policy: RetryPolicy | None = None,
) -> StoreHostResponse:
    active_policy = policy or RetryPolicy()
    retryable_codes = set(retryable_business_codes or ())
    retry_classifier = Retry(
        total=False,
        status_forcelist=active_policy.status_forcelist,
        allowed_methods=None,
    )
    ledger = {"http_failures": 0, "business_failures": 0}

    retrying = Retrying(
        stop=stop_after_attempt(active_policy.total_attempts),
        wait=wait_random_exponential(multiplier=1, max=30),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )

    for attempt in retrying:
        with attempt:
            try:
                request = getattr(session, "request", None)
                kwargs = {
                    "headers": headers,
                    "json": json_body,
                    "data": _request_body(data),
                    "timeout": timeout or active_policy.timeout_seconds,
                }
                if request is not None:
                    response = request(method, url, **kwargs)
                else:
                    response = getattr(session, method.lower())(url, **kwargs)
            except requests.RequestException as exc:
                ledger["http_failures"] += 1
                retryable = ledger["http_failures"] < active_policy.http_attempts
                raise RetryableTransferError(
                    stage,
                    redact_secrets(str(exc) or "StoreHost request failed"),
                    retryable=retryable,
                ) from exc

            status_code = int(getattr(response, "status_code", 0) or 0)
            if retry_classifier.is_retry(method.upper(), status_code):
                ledger["http_failures"] += 1
                retryable = ledger["http_failures"] < active_policy.http_attempts
                raise RetryableTransferError(
                    stage,
                    f"StoreHost HTTP {status_code}: {_safe_response_text(response)}",
                    retryable=retryable,
                )
            if status_code >= 400:
                raise NonRetryableTransferError(
                    stage,
                    f"StoreHost HTTP {status_code}: {_safe_response_text(response)}",
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise NonRetryableTransferError(
                    stage,
                    f"StoreHost response is not JSON: {_safe_response_text(response)}",
                ) from exc
            if not isinstance(body, dict):
                raise NonRetryableTransferError(
                    stage,
                    "StoreHost response JSON must be an object",
                )
            if "data" not in body:
                raise NonRetryableTransferError(
                    stage,
                    f"StoreHost response missing data: {redact_secrets(body)}",
                )
            data_block = body.get("data")
            if not isinstance(data_block, dict):
                raise NonRetryableTransferError(
                    stage,
                    f"StoreHost response data must be an object: "
                    f"{redact_secrets(body)}",
                )

            code = body.get("code")
            if expected_code is None:
                code_ok = code in (None, 0)
            else:
                code_ok = code == expected_code
            if code_ok:
                return StoreHostResponse(
                    data=data_block,
                    headers=dict(getattr(response, "headers", {}) or {}),
                    status_code=status_code,
                    raw_body=body,
                )

            code_text = _message_from_body(
                body, f"StoreHost business code {code!r} for {stage}"
            )
            if code in retryable_codes:
                ledger["business_failures"] += 1
                retryable = (
                    ledger["business_failures"] < active_policy.business_attempts
                )
                raise RetryableTransferError(
                    stage,
                    code_text,
                    retryable=retryable,
                )
            raise NonRetryableTransferError(stage, code_text)

    raise NonRetryableTransferError(stage, "StoreHost request failed unexpectedly")
