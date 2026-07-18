from __future__ import annotations

from utils.env import BOHRIUM_OPENAPI_BASE_COM

from .types import BohriumCredentials


def build_bohrium_env(credentials: BohriumCredentials) -> dict[str, str]:
    env: dict[str, str] = {}
    if credentials.access_key:
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
        # bohr-cli 只直读 BOHR_ACCESS_KEY（不认 BOHRIUM_ 前缀），注入后免 auth login
        env["BOHR_ACCESS_KEY"] = credentials.access_key
    env["BOHRIUM_OPENAPI_BASE_COM"] = BOHRIUM_OPENAPI_BASE_COM
    # bohr-cli 默认打生产 open.bohrium.com；test/uat 必须跟随本环境端点，
    # 否则本环境 AK 打生产返回 401，与凭证无效不可区分（prod 下该值即 CLI 默认值）
    env["BOHR_OPENAPI_HOST"] = BOHRIUM_OPENAPI_BASE_COM
    if credentials.project_id != -1:
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
    if credentials.user_id is not None:
        env["BOHRIUM_USER_ID"] = str(credentials.user_id)
    if credentials.user_no:
        env["BOHRIUM_USER_NO"] = credentials.user_no
    if credentials.base_url:
        env["BOHRIUM_BASE_URL"] = credentials.base_url
    return env
