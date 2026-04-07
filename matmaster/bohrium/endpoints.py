from __future__ import annotations

import os


def get_bohrium_service_env() -> str:
    raw = (os.getenv("SERVICE_ENV", "test") or "").strip().lower()
    return raw or "test"


def get_bohrium_base_url() -> str:
    override = (os.getenv("BOHRIUM_BASE_URL", "") or "").strip().rstrip("/")
    if override:
        return override
    service_env = get_bohrium_service_env()
    if service_env == "prod":
        return "https://openapi.dp.tech"
    return f"https://openapi.{service_env}.dp.tech"
