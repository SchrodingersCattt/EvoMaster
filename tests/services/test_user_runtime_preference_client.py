from unittest.mock import MagicMock


class _FakeResponse:
    def __init__(self, *, status_code=200, body=None, text=''):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, headers=None):
        self.requests.append((url, headers))
        return self.response


def test_get_user_runtime_preference_combines_tools_preference_and_latest_org(
    monkeypatch,
):
    from clients import user_runtime_preference_client as client_mod
    from src.services import user_runtime_preference_service as service_mod

    fake_client = _FakeClient(
        _FakeResponse(
            body={
                'code': 0,
                'data': {
                    'last_selected_project_id': '42',
                    'last_selected_model': 'matmaster/qwen',
                },
            }
        )
    )
    monkeypatch.setattr(client_mod, 'MATMASTER_TOOLS_SERVER', 'https://tools.example')
    monkeypatch.setattr(client_mod.httpx, 'Client', lambda timeout: fake_client)
    table = MagicMock()
    table.get_latest_org_id_by_user.return_value = 'org-1'

    pref = service_mod.get_user_runtime_preference('u1', table=table)

    assert pref.project_id == 42
    assert pref.model == 'matmaster/qwen'
    assert pref.org_id == 'org-1'
    assert fake_client.requests == [
        (
            'https://tools.example/api/v1/users/u1/runtime-preference',
            {'X-User-Id': 'u1'},
        )
    ]
    table.get_latest_org_id_by_user.assert_called_once_with('u1')


def test_get_user_runtime_preference_fail_soft_when_tools_server_fails(
    monkeypatch,
):
    from clients import user_runtime_preference_client as client_mod
    from src.services import user_runtime_preference_service as service_mod

    def _raise_client(timeout):
        raise client_mod.httpx.ConnectError('boom')

    monkeypatch.setattr(client_mod, 'MATMASTER_TOOLS_SERVER', 'https://tools.example')
    monkeypatch.setattr(client_mod.httpx, 'Client', _raise_client)
    table = MagicMock()
    table.get_latest_org_id_by_user.return_value = 'org-1'

    pref = service_mod.get_user_runtime_preference('u1', table=table)

    assert pref.project_id is None
    assert pref.model is None
    assert pref.org_id == 'org-1'
