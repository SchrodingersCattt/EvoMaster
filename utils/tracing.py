from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

logger = logging.getLogger(__name__)

_INITIALIZED = False
_TRACER_PROVIDER: Any | None = None
_REQUESTS_INSTRUMENTED = False


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _normalize_otlp_endpoint(raw: str) -> str:
    endpoint = raw.strip()
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return f"https://{endpoint}"


def _trace_headers() -> dict[str, str]:
    return {
        "x-sls-otel-project": _env("TRACE_PROJECT"),
        "x-sls-otel-instance-id": _env("TRACE_INSTANCE_ID"),
        "x-sls-otel-ak-id": _env("TRACE_AK"),
        "x-sls-otel-ak-secret": _env("TRACE_SK"),
    }


def configure_tracing(service_name: str) -> bool:
    """Configure OpenTelemetry tracing when TRACE_* env vars are present."""
    global _INITIALIZED, _REQUESTS_INSTRUMENTED, _TRACER_PROVIDER

    if _INITIALIZED:
        return True
    if _env("OTEL_SDK_DISABLED").lower() == "true":
        logger.info(
            "OpenTelemetry tracing disabled service=%s reason=OTEL_SDK_DISABLED",
            service_name,
        )
        return False

    endpoint = _env("TRACE_EXPORTER_ENDPOINT")
    if not endpoint:
        logger.info(
            "OpenTelemetry tracing disabled service=%s reason=missing TRACE_EXPORTER_ENDPOINT",
            service_name,
        )
        return False

    required = {
        "TRACE_PROJECT": _env("TRACE_PROJECT"),
        "TRACE_INSTANCE_ID": _env("TRACE_INSTANCE_ID"),
        "TRACE_AK": _env("TRACE_AK"),
        "TRACE_SK": _env("TRACE_SK"),
    }
    missing = sorted(key for key, value in required.items() if not value)
    if missing:
        logger.warning(
            "OpenTelemetry tracing disabled: missing env keys=%s",
            ",".join(missing),
        )
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry tracing disabled: import failed: %s", exc)
        return False

    normalized_endpoint = _normalize_otlp_endpoint(endpoint)
    service = _env("OTEL_SERVICE_NAME") or service_name
    resource_attrs: dict[str, Any] = {
        "service.name": service,
        "service.namespace": _env("OTEL_SERVICE_NAMESPACE") or "matmaster",
        "service.instance.id": _env("TRACE_INSTANCE_ID"),
        "host.name": socket.gethostname(),
    }
    if env := _env("SERVICE_ENV"):
        resource_attrs["deployment.environment"] = env
    if logstore := _env("TRACE_LOGSTORE"):
        resource_attrs["sls.logstore"] = logstore

    exporter_kwargs: dict[str, Any] = {
        "endpoint": normalized_endpoint,
        "headers": _trace_headers(),
    }
    if normalized_endpoint.startswith("http://"):
        exporter_kwargs["insecure"] = True

    provider = TracerProvider(resource=Resource.create(resource_attrs))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs)))
    trace.set_tracer_provider(provider)
    RequestsInstrumentor().instrument()
    _REQUESTS_INSTRUMENTED = True
    _TRACER_PROVIDER = provider
    _INITIALIZED = True
    logger.info(
        "OpenTelemetry tracing configured service=%s endpoint=%s project=%s instance=%s",
        service,
        normalized_endpoint,
        _env("TRACE_PROJECT"),
        _env("TRACE_INSTANCE_ID"),
    )
    return True


def shutdown_tracing(*, timeout_millis: int = 30000) -> bool:
    """Flush and shutdown the local tracer provider before process exit."""
    global _INITIALIZED, _REQUESTS_INSTRUMENTED, _TRACER_PROVIDER

    provider = _TRACER_PROVIDER
    if provider is None:
        if _REQUESTS_INSTRUMENTED:
            _uninstrument_requests()
            _REQUESTS_INSTRUMENTED = False
        return False
    try:
        provider.force_flush(timeout_millis=timeout_millis)
        provider.shutdown()
        logger.info("OpenTelemetry tracing shutdown complete")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry tracing shutdown failed: %s", exc)
        return False
    finally:
        if _REQUESTS_INSTRUMENTED:
            _uninstrument_requests()
            _REQUESTS_INSTRUMENTED = False
        _TRACER_PROVIDER = None
        _INITIALIZED = False


def get_tracer(name: str):
    from opentelemetry import trace

    return trace.get_tracer(name)


def _uninstrument_requests() -> None:
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().uninstrument()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry requests uninstrument failed: %s", exc)


def set_log_context_attributes(span) -> None:
    try:
        from src.utils.logger import LogContext
    except Exception:  # noqa: BLE001
        return

    session_id, task_id = LogContext.current()
    if session_id and session_id != "-":
        span.set_attribute("matmaster.session_id", session_id)
    if task_id and task_id != "-":
        span.set_attribute("matmaster.task_id", task_id)


def set_bohrium_http_request_attributes(
    span,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> None:
    """Attach the full Bohrium HTTP request for debugging platform submissions."""

    span.set_attribute("http.request.method", method)
    span.set_attribute("url.full", url)
    span.set_attribute(
        "bohrium.request.headers_json",
        json.dumps(headers, ensure_ascii=False, sort_keys=True),
    )
    span.set_attribute(
        "bohrium.request.body_json",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    )


def record_span_exception(span, exc: Exception) -> None:
    from opentelemetry.trace import Status, StatusCode

    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))
