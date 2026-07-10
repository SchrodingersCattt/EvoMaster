import clients.matmaster_platform.quota as mod


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = payload
        self.raised = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        self.raised = True

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, url, headers=None, params=None):
        self.calls.append((url, headers, params))
        return self.response


async def test_fetch_quota_info_returns_data(monkeypatch):
    response = _FakeResponse({"code": 0, "data": {"credit_remaining": 1.5}})
    session = _FakeSession(response)
    monkeypatch.setattr(mod, "MATMASTER_TOOLS_SERVER", "https://tools.example")
    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda: session)

    data = await mod.fetch_quota_info("u1")

    assert data == {"credit_remaining": 1.5}
    assert response.raised is True
    assert session.calls == [
        ("https://tools.example/api/v1/quota/info", {"X-User-Id": "u1"}, None)
    ]


async def test_fetch_quota_info_passes_project_id_as_query(monkeypatch):
    response = _FakeResponse({"code": 0, "data": {"org_wallet_pass": True}})
    session = _FakeSession(response)
    monkeypatch.setattr(mod, "MATMASTER_TOOLS_SERVER", "https://tools.example")
    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda: session)

    data = await mod.fetch_quota_info("u1", project_id=12791)

    assert data == {"org_wallet_pass": True}
    assert session.calls == [
        (
            "https://tools.example/api/v1/quota/info",
            {"X-User-Id": "u1"},
            {"project_id": "12791"},
        )
    ]


async def test_fetch_quota_info_non_dict_data_returns_empty(monkeypatch):
    session = _FakeSession(_FakeResponse({"code": 0, "data": ["bad"]}))
    monkeypatch.setattr(mod, "MATMASTER_TOOLS_SERVER", "https://tools.example")
    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda: session)

    assert await mod.fetch_quota_info("u1") == {}
