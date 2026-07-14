from __future__ import annotations

import pytest

from src.services import bohrium_node_service as node_module
from src.services.bohrium_node_service import (
    BohriumNodeNotFoundError,
    BohriumNodeService,
)
from src.utils.logger import LogContext


class _FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}
        self.exceptions: list[Exception] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)

    def set_status(self, status) -> None:
        self.attributes["otel.status"] = status


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str) -> _FakeSpan:
        span = _FakeSpan(name)
        self.spans.append(span)
        return span


class _FakeResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _FakeClient:
    def __init__(
        self,
        response: _FakeResponse,
        captured: dict[str, object],
    ) -> None:
        self._response = response
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self._captured.update({"url": url, "headers": headers, "json": json})
        return self._response


def _service() -> BohriumNodeService:
    service = BohriumNodeService()
    service._host = "https://openapi.test.dp.tech"
    return service


def test_create_node_emits_trace_span_without_access_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer = _FakeTracer()
    captured: dict[str, object] = {}
    response = _FakeResponse({"code": 0, "data": {"id": 123}})

    monkeypatch.setattr(node_module, "_TRACER", tracer)
    monkeypatch.setattr(
        node_module,
        "inject_trace_context",
        lambda headers: {**headers, "traceparent": "00-trace-span-01"},
    )
    monkeypatch.setattr(
        node_module.httpx,
        "Client",
        lambda timeout: _FakeClient(response, captured),
    )

    try:
        LogContext.bind("session-1", "task-1")
        result = _service().create_node(
            "secret-ak",
            42,
            name="matmaster-session",
            image_id=49106,
            sku_id=388,
            disk_size=40,
            turnoff_after=-1,
        )
    finally:
        LogContext.clear()

    assert result == {"node_id": 123, "ip": None, "password": None}
    assert captured["headers"]["accessKey"] == "secret-ak"
    assert captured["headers"]["traceparent"] == "00-trace-span-01"

    span = tracer.spans[0]
    assert span.name == "bohrium.node.create"
    assert span.attributes["matmaster.session_id"] == "session-1"
    assert span.attributes["matmaster.task_id"] == "task-1"
    assert span.attributes["bohrium.openapi.path"] == "/openapi/v1/node/add"
    assert span.attributes["bohrium.project_id"] == 42
    assert span.attributes["bohrium.image_id"] == 49106
    assert span.attributes["bohrium.sku_id"] == 388
    assert span.attributes["bohrium.response.code"] == 0
    assert span.attributes["bohrium.node_id"] == "123"
    assert '"projectId": 42' in span.attributes["bohrium.request.body_json"]
    assert "secret-ak" not in span.attributes["bohrium.request.body_json"]


def test_create_node_records_platform_code_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer = _FakeTracer()
    captured: dict[str, object] = {}
    response = _FakeResponse(
        {
            "code": 148888,
            "error": {"title": "", "msg": "record not found", "reference": ""},
            "isShowMsg": True,
        }
    )

    monkeypatch.setattr(node_module, "_TRACER", tracer)
    monkeypatch.setattr(
        node_module.httpx,
        "Client",
        lambda timeout: _FakeClient(response, captured),
    )

    with pytest.raises(RuntimeError, match="Bohrium create node failed"):
        _service().create_node(
            "secret-ak",
            42,
            name="matmaster-session",
            image_id=49106,
            sku_id=388,
            disk_size=40,
            turnoff_after=-1,
        )

    span = tracer.spans[0]
    assert span.name == "bohrium.node.create"
    assert span.attributes["bohrium.response.code"] == 148888
    assert len(span.exceptions) == 1
    assert "record not found" in str(span.exceptions[0])
    assert "secret-ak" not in span.attributes["bohrium.request.body_json"]


def test_stop_node_uses_pause_contract_instead_of_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _StopResponse(_FakeResponse):
        content = b'{"code": 0}'

    monkeypatch.setattr(
        node_module.httpx,
        "Client",
        lambda timeout: _FakeClient(_StopResponse({"code": 0}), captured),
    )

    _service().stop_node("secret-ak", 123, 42, creator_id=110680)

    assert captured["url"].endswith("/openapi/v1/node/stop/123")
    assert captured["json"] == {
        "creatorId": 110680,
        "projectId": 42,
        "device": "container",
        "stopType": 1,
    }


def test_stop_node_reports_provider_deleted_node(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class _MissingResponse(_FakeResponse):
        content = b'{"code": 404}'
        status_code = 404

    monkeypatch.setattr(
        node_module.httpx,
        "Client",
        lambda timeout: _FakeClient(_MissingResponse({"code": 404}), captured),
    )

    with pytest.raises(BohriumNodeNotFoundError):
        _service().stop_node("secret-ak", 123, 42, creator_id=110680)
