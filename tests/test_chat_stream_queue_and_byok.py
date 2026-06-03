import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


def test_chat_send_request_rejects_byok_with_model_or_llm():
    from src.models.chat import ChatSendRequest

    with pytest.raises(ValidationError):
        ChatSendRequest(
            content='hello',
            custom_llm_config_id=12,
            model='claude-sonnet-4-6',
        )
    with pytest.raises(ValidationError):
        ChatSendRequest(
            content='hello',
            custom_llm_config_id=12,
            llm='opus',
        )


def test_chat_send_request_accepts_byok_alone():
    from src.models.chat import ChatSendRequest

    req = ChatSendRequest(content='hello', custom_llm_config_id=12)

    assert req.custom_llm_config_id == 12
    assert req.model is None
    assert req.llm is None


class _FakeByokResolver:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def resolve_for_preflight(self, **kwargs):
        from src.models.byok import BYOKRunReference

        self.calls.append(kwargs)
        return SimpleNamespace(
            ref=BYOKRunReference(
                config_id=kwargs['config_id'],
                version=3,
                display_name='Research Proxy',
                model='model-a',
            )
        )


class _FakeChatService:
    def can_access_session(self, *_args, **_kwargs) -> bool:
        return True

    def ensure_session(self, *_args, **_kwargs) -> None:
        return None


class _FakeStreamService:
    def __init__(self) -> None:
        self.prepare_calls: list[tuple[tuple, dict]] = []

    def prepare_send_message(self, *args, **kwargs):
        self.prepare_calls.append((args, kwargs))
        return SimpleNamespace()

    async def generate_send_stream(self, session_id, _base_prompt, _ctx):
        yield (
            'event: ag-ui\n'
            f'data: {json.dumps({"type": "stream_closed", "session_id": session_id})}\n\n'
        )


class _FakeImageInputService:
    def __init__(self) -> None:
        self.validate_calls: list[dict] = []
        self.ensure_vision_supported_calls = 0

    def validate_current_images(self, *, files, images):
        self.validate_calls.append({'files': list(files), 'images': list(images)})
        return [SimpleNamespace(url=image) for image in images]

    def ensure_vision_supported(self, **_kwargs) -> None:
        self.ensure_vision_supported_calls += 1


def _install_chat_stream_overrides(monkeypatch):
    from app import app
    from src.apis import chat_api
    from src.services.events_service import get_events_service
    from src.services.sessions_service import get_sessions_service
    from src.services.stream_service import get_stream_service

    chat_svc = _FakeChatService()
    stream_svc = _FakeStreamService()
    resolver = _FakeByokResolver()
    app.dependency_overrides[get_sessions_service] = lambda: chat_svc
    app.dependency_overrides[get_stream_service] = lambda: stream_svc
    app.dependency_overrides[get_events_service] = lambda: MagicMock()
    monkeypatch.setattr(chat_api, 'REDIS_URL', 'redis://test')
    monkeypatch.setattr(
        chat_api,
        'get_byok_model_resolver',
        lambda: resolver,
        raising=False,
    )
    return app, chat_api, stream_svc, resolver


def _clear_chat_stream_overrides(app) -> None:
    from src.services.events_service import get_events_service
    from src.services.sessions_service import get_sessions_service
    from src.services.stream_service import get_stream_service

    app.dependency_overrides.pop(get_sessions_service, None)
    app.dependency_overrides.pop(get_stream_service, None)
    app.dependency_overrides.pop(get_events_service, None)


def test_chat_stream_byok_preflight_passes_reference_and_checks_quota_status(
    monkeypatch,
):
    app, chat_api, stream_svc, resolver = _install_chat_stream_overrides(monkeypatch)
    quota_calls: list[str] = []

    async def _check_quota_status(user_id: str):
        from src.services.quota_service import QuotaStatus

        quota_calls.append(user_id)
        return QuotaStatus(remaining_yuan=10.0, reset_at=None)

    monkeypatch.setattr(chat_api, 'check_quota_status', _check_quota_status)
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            '/api/v1/chat/sessions/sess-byok/stream',
            headers={'X-User-Id': 'user-1'},
            json={
                'content': 'hello',
                'mode': 'direct',
                'custom_llm_config_id': 12,
            },
        )
    finally:
        _clear_chat_stream_overrides(app)

    assert response.status_code == 200, response.text
    assert quota_calls == ['user-1']
    assert resolver.calls == [
        {
            'user_id': 'user-1',
            'config_id': 12,
            'mode': 'direct',
            'has_images': False,
        }
    ]
    assert stream_svc.prepare_calls[0][1]['byok_ref'].config_id == 12


def test_chat_stream_byok_without_user_id_returns_401(monkeypatch):
    app, chat_api, _stream_svc, resolver = _install_chat_stream_overrides(monkeypatch)
    quota_calls: list[str] = []

    async def _check_quota_status(user_id: str):
        from src.services.quota_service import QuotaStatus

        quota_calls.append(user_id)
        return QuotaStatus(remaining_yuan=10.0, reset_at=None)

    monkeypatch.setattr(chat_api, 'check_quota_status', _check_quota_status)
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            '/api/v1/chat/sessions/sess-byok/stream',
            json={
                'content': 'hello',
                'mode': 'direct',
                'custom_llm_config_id': 12,
            },
        )
    finally:
        _clear_chat_stream_overrides(app)

    assert response.status_code == 401, response.text
    assert response.json()['data']['error_code'] == 'byok_requires_user'
    assert quota_calls == []
    assert resolver.calls == []


def test_chat_stream_byok_with_images_does_not_call_static_vision_gate(monkeypatch):
    app, chat_api, _stream_svc, resolver = _install_chat_stream_overrides(monkeypatch)
    image_service = _FakeImageInputService()

    async def _check_quota_status(_user_id: str):
        from src.services.quota_service import QuotaStatus

        return QuotaStatus(remaining_yuan=10.0, reset_at=None)

    monkeypatch.setattr(chat_api, 'check_quota_status', _check_quota_status)
    monkeypatch.setattr(chat_api, 'get_image_input_service', lambda: image_service)
    monkeypatch.setattr(
        chat_api,
        'load_llm_config',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('static LLM config should not load for BYOK images')
        ),
    )
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            '/api/v1/chat/sessions/sess-byok/stream',
            headers={'X-User-Id': 'user-1'},
            json={
                'content': '看图',
                'mode': 'direct',
                'custom_llm_config_id': 12,
                'images': ['https://oss.example.com/chat/a.png'],
            },
        )
    finally:
        _clear_chat_stream_overrides(app)

    assert response.status_code == 200, response.text
    assert resolver.calls[0]['has_images'] is True
    assert image_service.validate_calls == [
        {
            'files': [],
            'images': ['https://oss.example.com/chat/a.png'],
        }
    ]
    assert image_service.ensure_vision_supported_calls == 0


def test_preset_model_uses_monetary_quota_status(monkeypatch):
    app, chat_api, _stream_svc, resolver = _install_chat_stream_overrides(monkeypatch)
    quota_calls: list[str] = []

    async def _check_quota_status(user_id: str):
        from src.services.quota_service import QuotaStatus

        quota_calls.append(user_id)
        return QuotaStatus(remaining_yuan=10.0, reset_at=None)

    monkeypatch.setattr(chat_api, 'check_quota_status', _check_quota_status)
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            '/api/v1/chat/sessions/sess-preset/stream',
            headers={'X-User-Id': 'user-1'},
            json={
                'content': 'hello',
                'mode': 'direct',
                'model': 'claude-sonnet-4-6',
            },
        )
    finally:
        _clear_chat_stream_overrides(app)

    assert response.status_code == 200, response.text
    assert quota_calls == ['user-1']
    assert resolver.calls == []


def test_preset_model_still_uses_static_vision_gate(monkeypatch):
    app, chat_api, _stream_svc, resolver = _install_chat_stream_overrides(monkeypatch)
    image_service = _FakeImageInputService()
    fake_llm_config = MagicMock()
    load_calls: list[object] = []

    async def _check_quota_status(_user_id: str):
        from src.services.quota_service import QuotaStatus

        return QuotaStatus(remaining_yuan=10.0, reset_at=None)

    def _load_llm_config(path):
        load_calls.append(path)
        return fake_llm_config

    monkeypatch.setattr(chat_api, 'check_quota_status', _check_quota_status)
    monkeypatch.setattr(chat_api, 'get_image_input_service', lambda: image_service)
    monkeypatch.setattr(chat_api, 'load_llm_config', _load_llm_config)
    try:
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            '/api/v1/chat/sessions/sess-preset/stream',
            headers={'X-User-Id': 'user-1'},
            json={
                'content': '看图',
                'mode': 'direct',
                'model': 'claude-sonnet-4-6',
                'images': ['https://oss.example.com/chat/a.png'],
            },
        )
    finally:
        _clear_chat_stream_overrides(app)

    assert response.status_code == 200, response.text
    assert resolver.calls == []
    assert load_calls
    assert image_service.validate_calls == [
        {
            'files': [],
            'images': ['https://oss.example.com/chat/a.png'],
        }
    ]
    assert image_service.ensure_vision_supported_calls == 1


def test_prepare_send_message_marks_explicit_bohrium_requirement():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(content='run', bohrium_project_id=42)

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
    ):
        ctx = service.prepare_send_message(
            'sess-1',
            req,
            user_id='user-1',
            org_id='org-1',
        )

    assert ctx is not None
    assert ctx.bohrium_required is True


def test_prepare_send_message_persists_images_in_user_message():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(
        content='看图',
        images=['https://oss.example.com/chat/a.png'],
    )

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
    ):
        ctx = service.prepare_send_message('sess-1', req, user_id='user-1')

    assert ctx is not None
    assert ctx.user_msg['images'] == ['https://oss.example.com/chat/a.png']
    assert events_service.add_history_event.call_args.args[1]['images'] == [
        'https://oss.example.com/chat/a.png'
    ]


def test_prepare_send_message_records_byok_metadata_without_secret():
    from src.models.byok import BYOKRunReference
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    deploy_state_service = MagicMock()
    fake_redis = MagicMock()
    byok_ref = BYOKRunReference(
        config_id=12,
        version=3,
        display_name='Research Proxy',
        model='model-a',
    )

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=deploy_state_service,
    )

    req = ChatSendRequest(content='run', custom_llm_config_id=12)

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
    ):
        ctx = service.prepare_send_message(
            'sess-1',
            req,
            user_id='user-1',
            byok_ref=byok_ref,
        )

    assert ctx is not None
    assert ctx.byok_ref.config_id == 12
    assert ctx.user_msg['requested_byok_config_id'] == 12
    assert ctx.user_msg['requested_byok_display_name'] == 'Research Proxy'
    assert ctx.user_msg['requested_model'] == 'model-a'
    user_msg_text = json.dumps(ctx.user_msg, ensure_ascii=False)
    assert 'api_key' not in user_msg_text
    assert 'api_key_cipher' not in user_msg_text
    assert 'sk-' not in user_msg_text
    events_service.add_history_event.assert_called_once()


@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_bohrium_required_flag():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    'source': 'System',
                    'type': 'status',
                    'content': '',
                    'session_id': 'sess-1',
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    ctx = SendStreamContext(
        task_id='task-1',
        invocation_id='inv-1',
        mode='direct',
        user_msg={'source': 'User', 'type': 'query', 'content': 'run'},
        request_event_queue=asyncio.Queue(),
        bohrium_required=True,
    )

    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.side_effect = lambda job: True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, 'close', None)
        if callable(close):
            close()
        return {
            'source': 'System',
            'type': 'stream_closed',
            'content': '',
            'session_id': 'sess-1',
        }

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
        patch('src.services.stream_service.notify_post_async'),
        patch(
            'src.services.stream_service.asyncio.wait_for',
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream('sess-1', 'run', ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job['bohrium_required'] is True


@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_images():
    from src.services.stream_service import ChatStreamService, SendStreamContext

    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    'source': 'System',
                    'type': 'status',
                    'content': '',
                    'session_id': 'sess-1',
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    ctx = SendStreamContext(
        task_id='task-1',
        invocation_id='inv-1',
        mode='direct',
        user_msg={'source': 'User', 'type': 'query', 'content': 'run'},
        request_event_queue=asyncio.Queue(),
        images=['https://oss.example.com/chat/a.png'],
    )

    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.side_effect = lambda job: True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, 'close', None)
        if callable(close):
            close()
        return {
            'source': 'System',
            'type': 'stream_closed',
            'content': '',
            'session_id': 'sess-1',
        }

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
        patch('src.services.stream_service.notify_post_async'),
        patch(
            'src.services.stream_service.asyncio.wait_for',
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream('sess-1', 'run', ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job['images'] == ['https://oss.example.com/chat/a.png']


@pytest.mark.asyncio
async def test_generate_send_stream_enqueues_byok_reference_only():
    from src.models.byok import BYOKRunReference
    from src.services.stream_service import ChatStreamService, SendStreamContext

    service = ChatStreamService(
        sessions_service=MagicMock(
            get_session_status_payload=MagicMock(
                return_value={
                    'source': 'System',
                    'type': 'status',
                    'content': '',
                    'session_id': 'sess-1',
                }
            )
        ),
        events_service=MagicMock(get_session_events=MagicMock(return_value=[])),
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )

    ctx = SendStreamContext(
        task_id='tid-1',
        invocation_id='inv-1',
        mode='direct',
        user_msg={'source': 'User', 'type': 'query', 'content': 'run'},
        request_event_queue=asyncio.Queue(),
        byok_ref=BYOKRunReference(
            config_id=12,
            version=3,
            display_name='Research Proxy',
            model='model-a',
        ),
    )

    fake_redis = MagicMock()
    fake_redis.create_client.return_value = None
    fake_redis.set_session_run_queued.return_value = True
    fake_redis.llen_agent_run_queue.return_value = 0
    fake_redis.lpush_agent_run_job.side_effect = lambda job: True

    async def _stream_closed_immediately(awaitable, timeout):
        close = getattr(awaitable, 'close', None)
        if callable(close):
            close()
        return {
            'source': 'System',
            'type': 'stream_closed',
            'content': '',
            'session_id': 'sess-1',
        }

    with (
        patch('src.services.stream_service.REDIS_URL', 'redis://test'),
        patch('src.services.stream_service.get_redis_dao', return_value=fake_redis),
        patch('src.services.stream_service.notify_post_async'),
        patch(
            'src.services.stream_service.asyncio.wait_for',
            side_effect=_stream_closed_immediately,
        ),
    ):
        gen = service.generate_send_stream('sess-1', 'run', ctx)
        await gen.__anext__()
        await gen.__anext__()
        await gen.__anext__()
        await gen.aclose()

    pushed_job = fake_redis.lpush_agent_run_job.call_args.args[0]
    assert pushed_job['byok'] == {'config_id': 12, 'version': 3}
    job_text = json.dumps(pushed_job, ensure_ascii=False)
    assert 'api_key' not in job_text
    assert 'api_key_cipher' not in job_text
    assert 'sk-' not in job_text
