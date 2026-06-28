"""Tests for fetch_byok_credential: model_params -> extra_body 解析与各错误路径。

异步 + mock httpx.AsyncClient，不连网络。
"""

from __future__ import annotations

import pytest

from clients.matmaster_platform import llm_credentials as mod
from clients.matmaster_platform.llm_credentials import (
    ByokCredentialError,
    fetch_byok_credential,
)


class _FakeResp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _patch_env(monkeypatch, *, bearer="svc-token", server="https://tools.example.com"):
    monkeypatch.setattr(mod, "MATMASTER_TOOLS_BYOK_BEARER", bearer)
    monkeypatch.setattr(mod, "MATMASTER_TOOLS_SERVER", server)


def _patch_client(monkeypatch, *, resp=None, exc=None):
    captured: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            if exc is not None:
                raise exc
            return resp

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return captured


def _ok_payload(**over):
    data = {
        "model": "qwen-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-plain",
    }
    data.update(over)
    return {"code": 0, "data": data}


async def test_success_parses_model_params_into_extra_body(monkeypatch):
    _patch_env(monkeypatch)
    captured = _patch_client(
        monkeypatch,
        resp=_FakeResp(
            200,
            _ok_payload(
                context_limit=1_000_000,
                model_params={"enable_thinking": True},
            ),
        ),
    )
    cred = await fetch_byok_credential(user_id="u1", credential_id="c1")
    assert cred.model == "qwen-max"
    assert cred.api_key == "sk-plain"
    assert cred.context_limit == 1_000_000
    assert cred.extra_body == {"enable_thinking": True}
    # 校验请求构造正确
    assert captured["url"].endswith("/api/v1/internal/llm-credentials/c1")
    assert captured["params"] == {"user_id": "u1"}
    assert captured["headers"]["Authorization"] == "Bearer svc-token"


async def test_success_without_model_params_defaults_empty(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(monkeypatch, resp=_FakeResp(200, _ok_payload()))
    cred = await fetch_byok_credential(user_id="u1", credential_id="c1")
    assert cred.extra_body == {}


async def test_model_params_non_dict_degrades_to_empty(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(monkeypatch, resp=_FakeResp(200, _ok_payload(model_params=["bad"])))
    cred = await fetch_byok_credential(user_id="u1", credential_id="c1")
    assert cred.extra_body == {}


async def test_invalid_context_limit_raises(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(monkeypatch, resp=_FakeResp(200, _ok_payload(context_limit=0)))
    with pytest.raises(ByokCredentialError, match="context_limit"):
        await fetch_byok_credential(user_id="u1", credential_id="c1")


async def test_missing_bearer_raises(monkeypatch):
    _patch_env(monkeypatch, bearer="")
    with pytest.raises(ByokCredentialError):
        await fetch_byok_credential(user_id="u1", credential_id="c1")


async def test_missing_ids_raises(monkeypatch):
    _patch_env(monkeypatch)
    with pytest.raises(ByokCredentialError):
        await fetch_byok_credential(user_id="", credential_id="c1")
    with pytest.raises(ByokCredentialError):
        await fetch_byok_credential(user_id="u1", credential_id="")


async def test_http_error_raises(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(monkeypatch, resp=_FakeResp(500, {}))
    with pytest.raises(ByokCredentialError):
        await fetch_byok_credential(user_id="u1", credential_id="c1")


async def test_business_error_code_raises_with_msg(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(
        monkeypatch,
        resp=_FakeResp(200, {"code": -9999, "data": None, "msg": "凭证不存在"}),
    )
    with pytest.raises(ByokCredentialError) as ei:
        await fetch_byok_credential(user_id="u1", credential_id="c1")
    assert "凭证不存在" in str(ei.value)


async def test_request_exception_wrapped(monkeypatch):
    _patch_env(monkeypatch)
    _patch_client(monkeypatch, exc=RuntimeError("conn refused"))
    with pytest.raises(ByokCredentialError):
        await fetch_byok_credential(user_id="u1", credential_id="c1")
