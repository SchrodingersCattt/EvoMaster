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


def test_fetch_bohrium_access_key_does_not_retry_no_items(monkeypatch):
    scripted = [_FakeResponse(200, {'code': 0, 'data': []})]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'no_items'
    assert result.attempts == 1


def test_fetch_bohrium_access_key_treats_blank_key_as_invalid(monkeypatch):
    scripted = [_FakeResponse(200, {'code': 0, 'data': [{'access_key': '   '} ]})]
    monkeypatch.setattr(
        'src.services.user_service.httpx.Client',
        lambda *args, **kwargs: _FakeClient(scripted),
    )

    result = UserService.fetch_bohrium_access_key_result('u1', 'o1')

    assert result.status == 'no_valid_ak'
    assert result.access_key is None


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
