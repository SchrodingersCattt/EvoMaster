from __future__ import annotations

import importlib
from typing import Any

from matmaster.integration.runtime_bridge.models import ResolvedCredential
from matmaster.integration.runtime_bridge.resolver import resolve_credentials

_ADAPTERS: dict[str, str] = {
    "bohrium": "matmaster.integration.runtime_bridge.adapters.bohrium",
}


def _get_adapter(service: str):  # noqa: ANN202
    if service not in _ADAPTERS:
        raise ValueError(f"Unknown service: {service!r}")
    return importlib.import_module(_ADAPTERS[service])


def resolve_service_credentials(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
) -> ResolvedCredential:
    adapter = _get_adapter(service)
    resolver_fn = getattr(adapter, f"resolve_{service}_credentials")
    return resolver_fn(session=session, explicit=explicit)


def build_service_env(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
) -> dict[str, str]:
    adapter = _get_adapter(service)
    env_fn = getattr(adapter, f"build_{service}_env")
    return env_fn(session=session, explicit=explicit)


def inject_mcp_args(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    adapter = _get_adapter(service)
    inject_fn = getattr(adapter, f"inject_{service}_mcp_args")
    return inject_fn(session=session, explicit=explicit, **kwargs)


__all__ = [
    "ResolvedCredential",
    "build_service_env",
    "inject_mcp_args",
    "resolve_service_credentials",
    "resolve_credentials",
]
