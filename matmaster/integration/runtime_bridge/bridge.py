"""Public entry points for the runtime credential bridge.

All service-specific logic is dispatched to adapters. Callers should use
the functions in this module (or import them from the package root).
"""

from __future__ import annotations

from typing import Any

from matmaster.integration.runtime_bridge.models import (
    OutputPathDecision,
    ResolvedCredential,
)
from matmaster.integration.runtime_bridge.path_policy import (
    resolve_output_path as _resolve_output_path,
)

# ── Adapter registry ────────────────────────────────────────────────

_ADAPTERS: dict[str, str] = {
    "bohrium": "matmaster.integration.runtime_bridge.adapters.bohrium",
}


def _get_adapter(service: str):  # noqa: ANN202
    """Lazily import and return the adapter module for *service*."""
    if service not in _ADAPTERS:
        raise ValueError(f"Unknown service: {service!r}")
    import importlib

    return importlib.import_module(_ADAPTERS[service])


# ── Public API ──────────────────────────────────────────────────────


def resolve_service_credentials(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
) -> ResolvedCredential:
    """Resolve credentials for *service* through the precedence chain.

    Dispatches to the service-specific adapter's ``resolve_<service>_credentials``.

    Args:
        service: Service identifier (e.g. ``"bohrium"``).
        session: Optional session object carrying embedded credentials.
        explicit: Optional caller-provided credential dict.

    Returns:
        A ``ResolvedCredential`` with source and values.
    """
    adapter = _get_adapter(service)
    resolver_fn = getattr(adapter, f"resolve_{service}_credentials")
    return resolver_fn(session=session, explicit=explicit)


def build_service_env(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build an env dict for *service* from resolved credentials.

    Dispatches to the service-specific adapter's ``build_<service>_env``.

    Args:
        service: Service identifier.
        session: Optional session object.
        explicit: Optional caller-provided credential dict.

    Returns:
        Dict of ``{ENV_VAR: value}`` strings.
    """
    adapter = _get_adapter(service)
    env_fn = getattr(adapter, f"build_{service}_env")
    return env_fn(session=session, explicit=explicit)


def resolve_output_path(
    *,
    raw_path: str,
    execution_workdir: str,
    session: Any | None = None,
) -> OutputPathDecision:
    """Classify an output path and decide if remote access is needed.

    Delegates to ``path_policy.resolve_output_path``.

    Args:
        raw_path: The path string as provided by the user or tool.
        execution_workdir: Current execution working directory.
        session: Optional remote execution session.

    Returns:
        An ``OutputPathDecision``.
    """
    return _resolve_output_path(
        raw_path=raw_path,
        execution_workdir=execution_workdir,
        session=session,
    )


def inject_mcp_args(
    service: str,
    *,
    session: Any | None = None,
    explicit: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Inject credentials into MCP executor args for *service*.

    Dispatches to the service-specific adapter's ``inject_<service>_mcp_args``.

    Args:
        service: Service identifier.
        session: Optional session object.
        explicit: Optional caller-provided credential dict.
        **kwargs: Additional service-specific arguments.

    Returns:
        Injected executor dict or empty dict.
    """
    adapter = _get_adapter(service)
    inject_fn = getattr(adapter, f"inject_{service}_mcp_args")
    return inject_fn(session=session, explicit=explicit, **kwargs)
