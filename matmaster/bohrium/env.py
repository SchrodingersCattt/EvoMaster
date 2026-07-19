from __future__ import annotations

import re

from utils.env import BOHRIUM_OPENAPI_BASE_COM

from .types import BohriumCredentials

_OPENAPI_BASE_RE = re.compile(r"https?://openapi(?:\.([a-z0-9-]+))?\.dp\.tech/?$")
_OPEN_HOST_RE = re.compile(r"https?://open(?:\.[a-z0-9-]+)?\.bohrium\.com/?$")


def _cli_openapi_host(base_url: str) -> str:
    """bohr-cli 的 host 属 open[.env].bohrium.com 族，环境必须与凭证一致。

    评测混环境流（SERVICE_ENV=test 但注入 prod 凭证）下不能用进程级常量推导，
    否则 prod AK 打 test 端点全量 401；按凭证自带的 base_url 推导环境，
    无法识别时才回落进程环境的 BOHRIUM_OPENAPI_BASE_COM。
    """
    raw = (base_url or "").strip().rstrip("/")
    if _OPEN_HOST_RE.match(raw):
        return raw
    m = _OPENAPI_BASE_RE.match(raw)
    if m:
        env_part = m.group(1)
        if env_part:
            return f"https://open.{env_part}.bohrium.com"
        return "https://open.bohrium.com"
    return BOHRIUM_OPENAPI_BASE_COM


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = {}
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
        # bohr-cli 只直读 BOHR_ACCESS_KEY（不认 BOHRIUM_ 前缀），注入后免 auth login
        env["BOHR_ACCESS_KEY"] = credentials.access_key
    env["BOHRIUM_OPENAPI_BASE_COM"] = BOHRIUM_OPENAPI_BASE_COM
    # bohr-cli 默认打生产 open.bohrium.com；host 必须与凭证所属环境一致，
    # 否则跨环境 401 与凭证无效不可区分（prod 凭证下该值即 CLI 默认值）
    env["BOHR_OPENAPI_HOST"] = _cli_openapi_host(credentials.base_url)
    if credentials.project_id != -1:
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
    if credentials.user_id is not None:
        env["BOHRIUM_USER_ID"] = str(credentials.user_id)
    if credentials.user_no:
        env["BOHRIUM_USER_NO"] = credentials.user_no
    if credentials.base_url:
        env["BOHRIUM_BASE_URL"] = credentials.base_url
    return env
