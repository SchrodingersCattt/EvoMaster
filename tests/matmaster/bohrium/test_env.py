"""build_bohrium_env 注入的 bohr-cli 直读变量（BOHR_ 前缀系）。

bohr-cli 定位为第三方工具、固定打生产端点：不注入 BOHR_OPENAPI_HOST，
BOHR_ACCESS_KEY 仅在凭证本身属生产环境时注入。
"""

from __future__ import annotations

from matmaster.bohrium.env import build_bohrium_env
from matmaster.bohrium.types import BohriumCredentials


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


def test_prod_credential_injects_bohr_access_key() -> None:
    env = build_bohrium_env(_credentials(base_url="https://openapi.dp.tech"))
    assert env["BOHR_ACCESS_KEY"] == "ak-test-123"
    assert env["BOHR_ACCESS_KEY"] == env["BOHRIUM_ACCESS_KEY"]

    env = build_bohrium_env(_credentials(base_url="https://open.bohrium.com/"))
    assert env["BOHR_ACCESS_KEY"] == "ak-test-123"


def test_non_prod_credential_skips_bohr_access_key() -> None:
    # test 凭证对生产端点必 401，注入只会制造假登录态；
    # BOHRIUM_ACCESS_KEY（matmaster 自用）不受影响。
    for base_url in ("https://openapi.test.dp.tech", "", "https://example.internal"):
        env = build_bohrium_env(_credentials(base_url=base_url))
        assert "BOHR_ACCESS_KEY" not in env
        assert env["BOHRIUM_ACCESS_KEY"] == "ak-test-123"


def test_no_access_key_injects_neither_ak_var() -> None:
    env = build_bohrium_env(
        _credentials(access_key="", base_url="https://openapi.dp.tech")
    )
    assert "BOHR_ACCESS_KEY" not in env
    assert "BOHRIUM_ACCESS_KEY" not in env


def test_bohr_openapi_host_never_injected() -> None:
    # bohr-cli 一律用默认生产端点，host 不随会话环境注入。
    for base_url in ("https://openapi.dp.tech", "https://openapi.test.dp.tech"):
        env = build_bohrium_env(_credentials(base_url=base_url))
        assert "BOHR_OPENAPI_HOST" not in env
