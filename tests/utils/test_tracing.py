from __future__ import annotations

from utils import tracing


def test_configure_tracing_disabled_without_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_INITIALIZED", False)
    monkeypatch.delenv("TRACE_EXPORTER_ENDPOINT", raising=False)

    assert tracing.configure_tracing("svc") is False


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
