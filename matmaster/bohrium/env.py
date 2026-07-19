from __future__ import annotations

import re

from utils.env import BOHRIUM_OPENAPI_BASE_COM

from .types import BohriumCredentials

# bohr-cli 定位为第三方工具：一律使用其默认的生产端点（open.bohrium.com），
# 不跟随会话环境，因此不注入 BOHR_OPENAPI_HOST。
_PROD_BASE_RE = re.compile(r"https?://(openapi\.dp\.tech|open\.bohrium\.com)/?$")


def _is_prod_credential(base_url: str) -> bool:
    return bool(_PROD_BASE_RE.match((base_url or "").strip()))


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = {}
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
        # bohr-cli 只直读 BOHR_ACCESS_KEY（不认 BOHRIUM_ 前缀），且固定打生产：
        # 仅生产凭证注入可免 auth login；test 凭证对生产端点必 401，
        # 注入只会制造假登录态，此时 agent 应走设备码或请用户提供生产 AK。
        if _is_prod_credential(credentials.base_url):
            env["BOHR_ACCESS_KEY"] = credentials.access_key
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
