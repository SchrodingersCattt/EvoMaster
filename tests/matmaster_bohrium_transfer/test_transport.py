from __future__ import annotations

import json

import pytest
from matmaster_bohrium_transfer.errors import (
    NonRetryableTransferError,
    RetryableTransferError,
)
from matmaster_bohrium_transfer.transport import (
    RetryPolicy,
    request_storehost_json,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_request_storehost_json_retries_http_500_then_returns_data() -> None:
    session = FakeSession(
        [
            FakeResponse(status_code=500, text="temporary"),
            FakeResponse(status_code=500, text="temporary"),
            FakeResponse(payload={"code": 0, "data": {"initialKey": "init-1"}}),
        ]
    )

    response = request_storehost_json(
        session,
        "POST",
        "https://store.example/api/upload/multipart/init",
        stage="multipart_init",
        json_body={"path": "prefix/input.zip"},
        policy=RetryPolicy(total_attempts=3, http_attempts=3),
    )

    assert response.data == {"initialKey": "init-1"}
    assert len(session.calls) == 3


def test_request_storehost_json_retries_retryable_business_code() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "code": 50001,
                    "message": "store busy",
                    "data": {},
                }
            ),
            FakeResponse(payload={"code": 0, "data": {"partString": "part-1"}}),
        ]
    )

    response = request_storehost_json(
        session,
        "POST",
        "https://store.example/api/upload/multipart/upload",
        stage="multipart_part",
        retryable_business_codes={50001},
        policy=RetryPolicy(total_attempts=2, business_attempts=2),
    )

    assert response.data == {"partString": "part-1"}
    assert len(session.calls) == 2


def test_request_storehost_json_raises_immediately_for_nonretryable_business_code():
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "code": 40001,
                    "message": "bad request",
                    "data": {},
                }
            ),
            FakeResponse(payload={"code": 0, "data": {"unexpected": True}}),
        ]
    )

    with pytest.raises(NonRetryableTransferError, match="bad request"):
        request_storehost_json(
            session,
            "POST",
            "https://store.example/api/upload/multipart/init",
            stage="multipart_init",
            retryable_business_codes={50001},
        )

    assert len(session.calls) == 1


def test_request_storehost_json_can_allow_success_without_data() -> None:
    session = FakeSession([FakeResponse(payload={"code": 0, "message": "ok"})])

    response = request_storehost_json(
        session,
        "POST",
        "https://store.example/api/upload/multipart/complete",
        stage="multipart_complete",
        allow_missing_data=True,
    )

    assert response.data == {}


def test_request_storehost_json_reports_business_error_without_data() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "code": 40001,
                    "message": "unsupported Content-MD5",
                }
            )
        ]
    )

    with pytest.raises(NonRetryableTransferError, match="unsupported Content-MD5"):
        request_storehost_json(
            session,
            "POST",
            "https://store.example/api/upload/multipart/upload",
            stage="multipart_part",
        )


def test_request_storehost_json_redacts_secrets_from_error_messages() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status_code=500,
                text="temporary failure for token=secret-token",
            )
        ]
    )

    with pytest.raises(RetryableTransferError) as exc_info:
        request_storehost_json(
            session,
            "POST",
            "https://store.example/api/upload/multipart/init",
            stage="multipart_init",
            policy=RetryPolicy(total_attempts=1, http_attempts=1),
        )

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "token=<redacted>" in message
