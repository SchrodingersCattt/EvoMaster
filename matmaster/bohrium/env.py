from __future__ import annotations

import os

from utils.env import BOHRIUM_OPENAPI_BASE_COM

from .types import BohriumCredentials

TRACE_ENV_KEYS = (
    "TRACE_EXPORTER_ENDPOINT",
    "TRACE_INSTANCE_ID",
    "TRACE_PROJECT",
    "TRACE_AK",
    "TRACE_SK",
    "TRACE_LOGSTORE",
)


def build_trace_env() -> dict[str, str]:
    return {
        key: value
        for key in TRACE_ENV_KEYS
        if (value := os.environ.get(key, "").strip())
    }


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = build_trace_env()
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
    env["BOHRIUM_OPENAPI_BASE_COM"] = BOHRIUM_OPENAPI_BASE_COM
    if credentials.project_id != -1:
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
    if credentials.user_id is not None:
        env["BOHRIUM_USER_ID"] = str(credentials.user_id)
    if credentials.user_no:
        env["BOHRIUM_USER_NO"] = credentials.user_no
    if credentials.base_url:
        env["BOHRIUM_BASE_URL"] = credentials.base_url
    return env
