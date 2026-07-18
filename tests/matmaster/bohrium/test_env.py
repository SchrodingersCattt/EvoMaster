"""build_bohrium_env 注入的 bohr-cli 直读变量（BOHR_ 前缀系）。"""

from __future__ import annotations

from matmaster.bohrium.env import build_bohrium_env
from matmaster.bohrium.types import BohriumCredentials
from utils.env import BOHRIUM_OPENAPI_BASE_COM


def _credentials(access_key: str = "ak-test-123") -> BohriumCredentials:
    return BohriumCredentials(
        access_key=access_key,
        project_id=123,
        user_id=7,
        user_no="u-007",
        base_url="https://openapi.test.dp.tech",
    )


def test_injects_bohr_access_key_alias_for_cli() -> None:
    env = build_bohrium_env(_credentials())
    assert env["BOHR_ACCESS_KEY"] == "ak-test-123"
    assert env["BOHR_ACCESS_KEY"] == env["BOHRIUM_ACCESS_KEY"]


def test_no_access_key_injects_neither_ak_var() -> None:
    env = build_bohrium_env(_credentials(access_key=""))
    assert "BOHR_ACCESS_KEY" not in env
    assert "BOHRIUM_ACCESS_KEY" not in env


def test_bohr_openapi_host_follows_env_openapi_base() -> None:
    # CLI 默认打生产 open.bohrium.com，注入值必须跟随本环境的 openapi base，
    # 否则 test AK 打生产 401 与凭证无效不可区分。
    env = build_bohrium_env(_credentials())
    assert env["BOHR_OPENAPI_HOST"] == BOHRIUM_OPENAPI_BASE_COM
    assert env["BOHR_OPENAPI_HOST"] == env["BOHRIUM_OPENAPI_BASE_COM"]
