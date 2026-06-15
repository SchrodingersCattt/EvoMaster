from __future__ import annotations

import logging

from src.utils.logger import LogContext
from utils import tracing


def test_configure_tracing_disabled_without_endpoint(monkeypatch, caplog) -> None:
    monkeypatch.setattr(tracing, "_INITIALIZED", False)
    monkeypatch.delenv("TRACE_EXPORTER_ENDPOINT", raising=False)

    with caplog.at_level(logging.INFO, logger=tracing.logger.name):
        assert tracing.configure_tracing("svc") is False

    assert "missing TRACE_EXPORTER_ENDPOINT" in caplog.text


def test_trace_headers_map_sls_env(monkeypatch) -> None:
    monkeypatch.setenv("TRACE_PROJECT", "project")
    monkeypatch.setenv("TRACE_INSTANCE_ID", "instance")
    monkeypatch.setenv("TRACE_AK", "ak")
    monkeypatch.setenv("TRACE_SK", "sk")

    assert tracing._trace_headers() == {
        "x-sls-otel-project": "project",
        "x-sls-otel-instance-id": "instance",
        "x-sls-otel-ak-id": "ak",
        "x-sls-otel-ak-secret": "sk",
    }


def test_normalize_otlp_endpoint_defaults_to_https() -> None:
    assert tracing._normalize_otlp_endpoint("example.com:10010") == (
        "https://example.com:10010"
    )
    assert tracing._normalize_otlp_endpoint("http://example.com:4317") == (
        "http://example.com:4317"
    )


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value

    def is_recording(self) -> bool:
        return True


def test_set_log_context_attributes() -> None:
    span = _FakeSpan()
    try:
        LogContext.bind("session-1", "task-1")
        tracing.set_log_context_attributes(span)
    finally:
        LogContext.clear()

    assert span.attributes == {
        "matmaster.session_id": "session-1",
        "matmaster.task_id": "task-1",
    }


class _FakeProvider:
    def __init__(self) -> None:
        self.force_flushed = False
        self.shutdown_called = False

    def force_flush(self, *, timeout_millis: int) -> None:
        assert timeout_millis == 1234
        self.force_flushed = True

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_shutdown_tracing_flushes_provider(monkeypatch) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(tracing, "_TRACER_PROVIDER", provider)
    monkeypatch.setattr(tracing, "_INITIALIZED", True)
    monkeypatch.setattr(tracing, "_REQUESTS_INSTRUMENTED", False)

    assert tracing.shutdown_tracing(timeout_millis=1234) is True

    assert provider.force_flushed is True
    assert provider.shutdown_called is True
    assert tracing._TRACER_PROVIDER is None
    assert tracing._INITIALIZED is False


def test_shutdown_tracing_uninstruments_requests(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(tracing, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(tracing, "_REQUESTS_INSTRUMENTED", True)
    monkeypatch.setattr(tracing, "_uninstrument_requests", lambda: calls.append("ok"))

    assert tracing.shutdown_tracing(timeout_millis=1234) is False

    assert calls == ["ok"]
    assert tracing._REQUESTS_INSTRUMENTED is False


def test_record_requests_trace_headers() -> None:
    class _Request:
        headers = {
            "traceparent": "00-497947a314b9d49abc6ae44dd11ba707-spanid0000000000-01",
            "tracestate": "vendor=value",
        }

    span = _FakeSpan()

    tracing._record_requests_trace_headers(span, _Request(), object())

    assert span.attributes == {
        "http.request.header.traceparent": (
            "00-497947a314b9d49abc6ae44dd11ba707-spanid0000000000-01"
        ),
        "http.request.header.tracestate": "vendor=value",
    }
