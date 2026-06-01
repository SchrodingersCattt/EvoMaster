"""evo 侧计费瘦客户端测试：校验上报 payload 与响应解析。"""

import pytest

from src.services.billing_service import BillingRunContext, BillingService


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
    """记录最后一次 POST，便于断言 url/headers/payload。"""

    last_post: dict = {}

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        _FakeSession.last_post = {"url": url, "headers": headers, "json": json}
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
async def test_report_llm_usage_posts_expected_payload(monkeypatch):
    session_cls = _make_session_cls(
        200, {"code": 0, "data": {"recorded": True, "pricing_status": "priced"}}
    )
    monkeypatch.setattr(
        "src.services.billing_service.aiohttp.ClientSession", session_cls
    )

    service = BillingService(base_url="https://tools.example.com")
    ok = await service.report_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=3,
        spawn_id="child-1",
        usage={"prompt_tokens": 1000, "completion_tokens": 200},
    )

    assert ok is True
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
async def test_report_llm_usage_skips_empty_usage(monkeypatch):
    session_cls = _make_session_cls(200, {"code": 0, "data": {"recorded": True}})
    monkeypatch.setattr(
        "src.services.billing_service.aiohttp.ClientSession", session_cls
    )
    session_cls.last_post = {}

    service = BillingService(base_url="https://tools.example.com")
    ok = await service.report_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage=None,
    )

    assert ok is False
    assert session_cls.last_post == {}


@pytest.mark.asyncio
async def test_report_llm_usage_reuses_provided_session(monkeypatch):
    """传入 session 时复用它，不再新建 ClientSession（一次 run 内共享连接池）。"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("should not create a new ClientSession")

    monkeypatch.setattr("src.services.billing_service.aiohttp.ClientSession", _boom)

    shared_cls = _make_session_cls(200, {"code": 0, "data": {"recorded": True}})
    shared = shared_cls()
    _FakeSession.last_post = {}

    service = BillingService(base_url="https://tools.example.com")
    ok = await service.report_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage={"prompt_tokens": 10},
        session=shared,
    )

    assert ok is True
    assert _FakeSession.last_post["url"] == (
        "https://tools.example.com/api/v1/billing/usage"
    )


@pytest.mark.asyncio
async def test_report_llm_usage_swallows_server_error(monkeypatch):
    session_cls = _make_session_cls(500, {"code": -1, "msg": "boom"})
    monkeypatch.setattr(
        "src.services.billing_service.aiohttp.ClientSession", session_cls
    )

    service = BillingService(base_url="https://tools.example.com")
    ok = await service.report_llm_usage(
        run_context=_ctx(),
        model="claude-sonnet-4-6",
        call_index=1,
        spawn_id=None,
        usage={"prompt_tokens": 10},
    )

    assert ok is False
