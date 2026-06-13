from __future__ import annotations

import httpx

from src.services.user_service import BohriumAccessKeyFetchResult, UserService


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, scripted):
        self._scripted = scripted

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers):
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, headers, json=None):
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fetch_bohrium_access_key_retries_on_timeout_then_succeeds(monkeypatch):
    scripted = [
        httpx.ReadTimeout('timeout'),
        _FakeResponse(200, {'code': 0, 'data': [{'access_key': 'ak-1'}]}),
    ]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'success'
    assert result.access_key == 'ak-1'
    assert result.attempts == 2
    assert result.retryable is False


def test_fetch_bohrium_access_key_creates_when_list_empty(monkeypatch):
    scripted = [
        _FakeResponse(200, {'code': 0, 'data': []}),
        _FakeResponse(
            200,
            {'code': 0, 'data': {'accessKey': 'ak-created'}},
        ),
    ]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'success'
    assert result.access_key == 'ak-created'
    assert result.attempts == 2


def test_fetch_bohrium_access_key_creates_when_list_has_only_blank_key(monkeypatch):
    scripted = [
        _FakeResponse(200, {'code': 0, 'data': [{'access_key': '   '}]}),
        _FakeResponse(
            200,
            {'code': 0, 'data': {'access_key': 'ak-after-blank'}},
        ),
    ]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'success'
    assert result.access_key == 'ak-after-blank'
    assert result.attempts == 2


def test_fetch_bohrium_access_key_returns_create_error_when_add_fails(monkeypatch):
    scripted = [
        _FakeResponse(200, {'code': 0, 'data': []}),
        _FakeResponse(400, {'code': 1, 'message': 'denied'}),
    ]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'ak_create_http_4xx'
    assert result.http_status == 400
    assert result.attempts == 2


def test_get_bohrium_access_key_keeps_cleanup_single_attempt_semantics(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_fetch(
        user_id: str | None,
        org_id: str | None,
        *,
        timeout: float = 2.0,
        retry_delays: tuple[float, ...] = (0.5, 1.0),
    ) -> BohriumAccessKeyFetchResult:
        captured['user_id'] = user_id
        captured['org_id'] = org_id
        captured['timeout'] = timeout
        captured['retry_delays'] = retry_delays
        return BohriumAccessKeyFetchResult(
            status='success',
            access_key='ak-cleanup',
            retryable=False,
        )

    monkeypatch.setattr(
        UserService,
        'fetch_bohrium_access_key_result',
        staticmethod(_fake_fetch),
    )

    result = UserService.get_bohrium_access_key('u1', 'o1')

    assert result == 'ak-cleanup'
    assert captured['user_id'] == 'u1'
    assert captured['org_id'] == 'o1'
    assert captured['timeout'] == 15.0
    assert captured['retry_delays'] == ()


def test_fetch_bohrium_access_key_can_skip_create_when_list_empty(monkeypatch):
    scripted = [
        _FakeResponse(200, {'code': 0, 'data': []}),
    ]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result(
        'u1', 'o1', retry_delays=(), create_if_missing=False
    )

    assert result.status == 'no_items'
    assert result.access_key is None
    assert result.attempts == 1
    assert scripted == []


def test_get_existing_bohrium_access_key_never_creates(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_fetch(
        user_id: str | None,
        org_id: str | None,
        *,
        timeout: float = 2.0,
        retry_delays: tuple[float, ...] = (0.5, 1.0),
        create_if_missing: bool = True,
    ) -> BohriumAccessKeyFetchResult:
        captured['user_id'] = user_id
        captured['org_id'] = org_id
        captured['timeout'] = timeout
        captured['retry_delays'] = retry_delays
        captured['create_if_missing'] = create_if_missing
        return BohriumAccessKeyFetchResult(status='no_items', retryable=False)

    monkeypatch.setattr(
        UserService,
        'fetch_bohrium_access_key_result',
        staticmethod(_fake_fetch),
    )

    result = UserService.get_existing_bohrium_access_key('u1', 'o1')

    assert result is None
    assert captured['user_id'] == 'u1'
    assert captured['org_id'] == 'o1'
    assert captured['timeout'] == 15.0
    assert captured['retry_delays'] == ()
    assert captured['create_if_missing'] is False
