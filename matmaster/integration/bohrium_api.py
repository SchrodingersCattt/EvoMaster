"""Shared Bohrium API endpoint helpers.

Keeps Bohrium host resolution consistent across MatMaster modules.
The repository bootstrap in ``utils.env`` defaults ``SERVICE_ENV`` to
``test`` when unset, so this helper mirrors that behavior instead of
silently assuming production.
"""

from __future__ import annotations

import os


def get_bohrium_service_env() -> str:
    """Return normalized Bohrium environment name.

    ``SERVICE_ENV`` defaults to ``test`` to match the repository bootstrap.
    """
    raw = (os.getenv("SERVICE_ENV", "test") or "").strip().lower()
    return raw or "test"


def get_bohrium_base_url() -> str:
    """Return the Bohrium OpenAPI base URL.

    Priority:
    1. ``BOHRIUM_BASE_URL`` explicit override
    2. ``SERVICE_ENV`` derived default:
       - ``prod`` -> ``https://open.bohrium.com``
       - others -> ``https://openapi.{env}.dp.tech``
    """
    override = (os.getenv("BOHRIUM_BASE_URL", "") or "").strip().rstrip("/")
    if override:
        return override

    service_env = get_bohrium_service_env()
    if service_env == "prod":
        return "https://open.bohrium.com"
    return f"https://openapi.{service_env}.dp.tech"
