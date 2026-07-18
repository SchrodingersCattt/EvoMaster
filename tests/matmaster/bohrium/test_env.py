"""build_bohrium_env 注入的 bohr-cli 直读变量（BOHR_ 前缀系）。"""

from __future__ import annotations

from matmaster.bohrium.env import build_bohrium_env
from matmaster.bohrium.types import BohriumCredentials
from utils.env import BOHRIUM_OPENAPI_BASE_COM


def _credentials(
    access_key: str = "ak-test-123",
    base_url: str = "https://openapi.test.dp.tech",
) -> BohriumCredentials:
    return BohriumCredentials(
        access_key=access_key,
        project_id=123,
        user_id=7,
        user_no="u-007",
        base_url=base_url,
    )


def test_injects_bohr_access_key_alias_for_cli() -> None:
    env = build_bohrium_env(_credentials())
    assert env["BOHR_ACCESS_KEY"] == "ak-test-123"
    assert env["BOHR_ACCESS_KEY"] == env["BOHRIUM_ACCESS_KEY"]


def test_no_access_key_injects_neither_ak_var() -> None:
    env = build_bohrium_env(_credentials(access_key=""))
    assert "BOHR_ACCESS_KEY" not in env
    assert "BOHRIUM_ACCESS_KEY" not in env


def test_bohr_openapi_host_follows_credential_env_not_service_env() -> None:
    # 评测混环境流（--bohrium-env prod 时 SERVICE_ENV 仍为 test）下，
    # host 必须跟随凭证的 base_url 环境，否则 prod AK 打 test 端点全量 401。
    env = build_bohrium_env(_credentials())
    assert env["BOHR_OPENAPI_HOST"] == "https://open.test.bohrium.com"

    env = build_bohrium_env(_credentials(base_url="https://openapi.dp.tech"))
    assert env["BOHR_OPENAPI_HOST"] == "https://open.bohrium.com"


def test_bohr_openapi_host_passthrough_and_fallback() -> None:
    env = build_bohrium_env(_credentials(base_url="https://open.uat.bohrium.com/"))
    assert env["BOHR_OPENAPI_HOST"] == "https://open.uat.bohrium.com"

    env = build_bohrium_env(_credentials(base_url="https://example.internal:8080"))
    assert env["BOHR_OPENAPI_HOST"] == BOHRIUM_OPENAPI_BASE_COM

    env = build_bohrium_env(_credentials(base_url=""))
    assert env["BOHR_OPENAPI_HOST"] == BOHRIUM_OPENAPI_BASE_COM
