from unittest.mock import MagicMock, patch

from clients.matmaster_platform.allowlist import (
    ALLOWLIST_RULE_ADMIN,
    _fetch_is_in_admin_allowlist_uncached,
    is_user_in_admin_allowlist,
    is_user_in_admin_allowlist_cached,
)


def test_fetch_parses_success_true():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": 0,
        "data": {"is_in_allowlist": True},
        "msg": "拥有权限",
    }
    with patch("clients.matmaster_platform.allowlist.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = mock_resp
        client_cls.return_value = client
        with patch(
            "clients.matmaster_platform.allowlist.MATMASTER_TOOLS_SERVER",
            "https://ts.example.com",
        ):
            assert _fetch_is_in_admin_allowlist_uncached("u1") is True
    call_kw = client.post.call_args
    assert ALLOWLIST_RULE_ADMIN in call_kw[1]["json"]["rules"]
    assert call_kw[1]["headers"]["X-User-Id"] == "u1"


def test_fetch_code_nonzero_false():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "code": 1,
        "data": {"is_in_allowlist": False},
        "msg": "err",
    }
    with patch("clients.matmaster_platform.allowlist.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = mock_resp
        client_cls.return_value = client
        with patch(
            "clients.matmaster_platform.allowlist.MATMASTER_TOOLS_SERVER",
            "https://ts.example.com",
        ):
            assert _fetch_is_in_admin_allowlist_uncached("u1") is False


def test_is_user_in_admin_allowlist_respects_cache(monkeypatch):
    calls = {"n": 0}

    def fake_uncached(uid: str) -> bool:
        calls["n"] += 1
        return True

    monkeypatch.setattr(
        "clients.matmaster_platform.allowlist._fetch_is_in_admin_allowlist_uncached",
        fake_uncached,
    )
    monkeypatch.setattr(
        "clients.matmaster_platform.allowlist._cache_bucket",
        lambda: 42,
    )
    is_user_in_admin_allowlist_cached.cache_clear()
    assert is_user_in_admin_allowlist("x") is True
    assert is_user_in_admin_allowlist("x") is True
    assert calls["n"] == 1
