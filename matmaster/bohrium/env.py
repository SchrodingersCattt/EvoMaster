from __future__ import annotations

from .types import BohriumCredentials


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = {}
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
    if credentials.project_id != -1:
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
    if credentials.user_id is not None:
        env["BOHRIUM_USER_ID"] = str(credentials.user_id)
    if credentials.user_no:
        env["BOHRIUM_USER_NO"] = credentials.user_no
    if credentials.base_url:
        env["BOHRIUM_BASE_URL"] = credentials.base_url
    return env
