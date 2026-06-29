"""evo 侧计费瘦客户端测试：校验上报 payload 与响应解析。"""

import pytest

from clients.matmaster_platform.billing import BillingRunContext, BillingService


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    """记录最后一次 POST/GET，便于断言 url/headers/payload。"""

    last_post: dict = {}
    last_get: dict = {}

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        _FakeSession.last_post = {"url": url, "headers": headers, "json": json}
        return _FakeResponse(self._status, self._payload)

    def get(self, url, params=None, headers=None, timeout=None):
        _FakeSession.last_get = {"url": url, "params": params, "headers": headers}
        return _FakeResponse(self._status, self._payload)

    async def close(self):
        return None


def _make_session_cls(status: int, payload: dict):
    return type(
        "BoundFakeSession",
        (_FakeSession,),
        {"_status": status, "_payload": payload},
    )


def _ctx() -> BillingRunContext:
    return BillingRunContext(session_id="s1", task_id="t1", invocation_id="i1")


@pytest.mark.asyncio
async def test_price_llm_usage_posts_expected_payload(monkeypatch):
    session_cls = _make_session_cls(
        200, {"code": 0, "data": {"recorded": True, "pricing_status": "priced"}}
    )
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", session_cls
    )

    service = BillingService(base_url="https://tools.example.com")
    data = await service.price_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=3,
        spawn_id="child-1",
        usage={"prompt_tokens": 1000, "completion_tokens": 200},
        billing_mode="platform",
    )

    assert data == {"recorded": True, "pricing_status": "priced"}
    sent = session_cls.last_post
    assert sent["url"] == "https://tools.example.com/api/v1/billing/usage"
    assert "Authorization" not in sent["headers"]
    body = sent["json"]
    assert body["session_id"] == "s1"
    assert body["call_index"] == 3
    assert body["spawn_id"] == "child-1"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["usage"] == {"prompt_tokens": 1000, "completion_tokens": 200}
    assert "user_id" not in body
    assert "org_id" not in body
    assert "project_id" not in body
    assert "provider" not in body
    assert "model_route" not in body
    assert "model_profile" not in body
    assert "call_kind" not in body
    assert "usage_vendor" not in body
    assert "billing_mode" not in body


@pytest.mark.asyncio
async def test_price_llm_usage_skips_empty_usage(monkeypatch):
    session_cls = _make_session_cls(200, {"code": 0, "data": {"recorded": True}})
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", session_cls
    )
    session_cls.last_post = {}

    service = BillingService(base_url="https://tools.example.com")
    data = await service.price_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage=None,
    )

    assert data is None
    assert session_cls.last_post == {}


@pytest.mark.asyncio
async def test_price_llm_usage_reuses_provided_session(monkeypatch):
    """传入 session 时复用它，不再新建 ClientSession（一次 run 内共享连接池）。"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("should not create a new ClientSession")

    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", _boom
    )

    shared_cls = _make_session_cls(200, {"code": 0, "data": {"recorded": True}})
    shared = shared_cls()
    _FakeSession.last_post = {}

    service = BillingService(base_url="https://tools.example.com")
    data = await service.price_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage={"prompt_tokens": 10},
        session=shared,
    )

    assert data == {"recorded": True}
    assert _FakeSession.last_post["url"] == (
        "https://tools.example.com/api/v1/billing/usage"
    )


@pytest.mark.asyncio
async def test_price_llm_usage_swallows_server_error(monkeypatch):
    session_cls = _make_session_cls(500, {"code": -1, "msg": "boom"})
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", session_cls
    )

    service = BillingService(base_url="https://tools.example.com")
    data = await service.price_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage={"prompt_tokens": 10},
    )

    assert data is None


@pytest.mark.asyncio
async def test_usage_post_carries_internal_bearer_when_configured(monkeypatch):
    # 配了内网服务 Bearer 时，上报须带 Authorization，否则 tools-server 鉴权会拒绝、计费静默丢失。
    session_cls = _make_session_cls(200, {"code": 0, "data": {"recorded": True}})
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", session_cls
    )
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.MATMASTER_TOOLS_INTERNAL_BEARER",
        "svc-key",
    )
    _FakeSession.last_post = {}

    service = BillingService(base_url="https://tools.example.com")
    await service.price_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage={"prompt_tokens": 10},
    )

    headers = _FakeSession.last_post["headers"]
    assert headers["Authorization"] == "Bearer svc-key"
    # 鉴权头不应顶掉原有 Content-Type。
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_run_cost_query_carries_internal_bearer_when_configured(monkeypatch):
    session_cls = _make_session_cls(200, {"code": 0, "data": {"total_amount_micro": 5}})
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.aiohttp.ClientSession", session_cls
    )
    monkeypatch.setattr(
        "clients.matmaster_platform.billing.client.MATMASTER_TOOLS_INTERNAL_BEARER",
        "svc-key",
    )
    _FakeSession.last_get = {}

    service = BillingService(base_url="https://tools.example.com")
    data = await service.get_run_cost("inv-1")

    assert data == {"total_amount_micro": 5}
    sent = _FakeSession.last_get
    assert sent["url"] == "https://tools.example.com/api/v1/billing/usage/summary"
    assert sent["params"] == {"invocation_id": "inv-1"}
    assert sent["headers"]["Authorization"] == "Bearer svc-key"
