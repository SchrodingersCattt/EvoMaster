"""Focused tests for Bohrium SSH setup/cleanup contract (runtime state, no mixin helpers)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import matmaster.config.loader as matmaster_loader
from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.types.cancellation import CancellationController
from matmaster.types.context import PlaygroundContext
from tests.matmaster.core.conftest import MockLLMProvider

_src_services = pytest.importorskip(
    "src.services.agent_run_bohrium",
    reason="src not available (isolation test)",
)
arb = _src_services
BohriumSetupResult = _src_services.BohriumSetupResult
BohriumSetupService = _src_services.BohriumSetupService
SESSIONS = pytest.importorskip(
    "src.services.sessions_service",
    reason="src not available (isolation test)",
).SESSIONS


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    SESSIONS.clear()
    yield
    SESSIONS.clear()


def _make_pg(original_session: MagicMock) -> MagicMock:
    pg = MagicMock()
    pg.session = original_session
    pg._owns_session = True
    pg.config = MagicMock()
    pg.config.model_dump.return_value = {
        'skills': {'skills_root': 'matmaster/skills/lazymcp'},
    }
    return pg


def _make_bohrium_service(sessions_service: Any | None = None) -> BohriumSetupService:
    return BohriumSetupService(
        sessions_service=sessions_service or MagicMock(),
        event_sink=lambda event: None,
    )


@patch.object(arb, '_run_clear_remote_proxy', MagicMock())
@patch.object(arb, '_remote_session_workspace_root', return_value='/share')
@patch('src.services.agent_run_bohrium.get_bohrium_nodes_table')
@patch('src.services.agent_run_bohrium.get_bohrium_node_service')
def test_successful_setup_returns_execution_binding_and_stores_runtime(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    """After attach, result carries execution_session/workdir/type and SESSIONS has runtime."""
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []

    node_svc.create_node.return_value = {'node_id': 42}
    node_svc.wait_until_ready.return_value = {
        'ip': '10.0.0.1',
        'password': 'secret',
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)

    mock_ssh = MagicMock()
    mock_ssh.is_open = True
    mock_ssh._env = MagicMock()
    mock_ssh._env.upload_directory_tarball = MagicMock(return_value=1)
    mock_ssh.remote_project_root = '/remote/proj'

    with patch.object(arb, 'SSHSession', return_value=mock_ssh) as mock_ssh_cls:
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id='sess-ok',
            pg=pg,
            run_creds={
                'access_key': 'ak',
                'project_id': 99,
            },
            user_id_for_ak='u1',
            org_id='o1',
            event_callback=MagicMock(),
            run_started_at=0.0,
        )

    assert result.ssh_attached is True
    assert result.abort_result is None
    assert result.execution_session is mock_ssh
    assert result.execution_workdir == '/share'
    assert result.session_type == 'ssh'

    assert pg.session is mock_ssh
    assert pg._owns_session is False

    mock_ssh_cls.assert_called_once()
    cfg = mock_ssh_cls.call_args[0][0]
    assert cfg.host == '10.0.0.1'
    assert cfg.password == 'secret'
    assert cfg.working_dir == '/share'

    mock_ssh.open.assert_called_once()

    rt = SESSIONS['sess-ok'].get('bohrium_runtime')
    assert rt is not None
    assert rt['original_session'] is original_session
    assert rt['original_owns_session'] is True
    assert rt['ssh_session'] is mock_ssh


@patch.object(arb, '_run_clear_remote_proxy', MagicMock())
@patch.object(arb, '_remote_session_workspace_root', return_value='/share')
@patch('src.services.agent_run_bohrium.get_bohrium_nodes_table')
@patch('src.services.agent_run_bohrium.get_bohrium_node_service')
def test_setup_does_not_emit_skills_synced_event(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    """Bohrium setup no longer owns skill directory sync telemetry."""
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []

    node_svc.create_node.return_value = {'node_id': 42}
    node_svc.wait_until_ready.return_value = {
        'ip': '10.0.0.1',
        'password': 'secret',
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)
    event_callback = MagicMock()

    class FakeSSHSession:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.is_open = False

        def open(self) -> None:
            self.is_open = True

        def close(self) -> None:
            self.is_open = False

    with patch.object(arb, 'SSHSession', new=FakeSSHSession):
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id='sess-no-skill-sync',
            pg=pg,
            run_creds={
                'access_key': 'ak',
                'project_id': 99,
            },
            user_id_for_ak='u1',
            org_id='o1',
            event_callback=event_callback,
            run_started_at=0.0,
        )

    assert result.ssh_attached is True
    assert not any(
        call.args[1] == 'bohrium_node'
        and isinstance(call.args[2], dict)
        and call.args[2].get('status') == 'skills_synced'
        for call in event_callback.call_args_list
    )


@patch.object(arb, '_run_clear_remote_proxy')
@patch.object(arb, '_remote_session_workspace_root', return_value='/share')
@patch('src.services.agent_run_bohrium.get_bohrium_nodes_table')
@patch('src.services.agent_run_bohrium.get_bohrium_node_service')
def test_setup_failure_after_open_restores_original_and_clears_runtime(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
    mock_run_clear_remote_proxy: MagicMock,
) -> None:
    """If setup fails after swap/store, restore the original playground session."""
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []

    node_svc.create_node.return_value = {'node_id': 42}
    node_svc.wait_until_ready.return_value = {
        'ip': '10.0.0.1',
        'password': 'secret',
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)

    mock_ssh = MagicMock()
    mock_ssh.is_open = True

    def _raise_after_store(pg_obj: object, phase: str) -> None:
        if phase == 'post_ssh':
            raise RuntimeError('post-store failure')

    with patch.object(arb, 'SSHSession', return_value=mock_ssh):
        mock_run_clear_remote_proxy.side_effect = _raise_after_store
        event_callback = MagicMock()
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id='sess-fail',
            pg=pg,
            run_creds={
                'access_key': 'ak',
                'project_id': 99,
            },
            user_id_for_ak='u1',
            org_id='o1',
            event_callback=event_callback,
            run_started_at=0.0,
        )

    assert result.ssh_attached is False
    assert result.abort_result is not None
    assert pg.session is original_session
    assert pg._owns_session is True
    assert 'bohrium_runtime' not in SESSIONS.get('sess-fail', {})
    mock_ssh.open.assert_called_once()
    mock_ssh.close.assert_called_once()
    mock_run_clear_remote_proxy.assert_called_once_with(pg, 'post_ssh')
    event_callback.assert_any_call(
        'System',
        'bohrium_node',
        {
            'status': 'failed',
            'message': 'Bohrium 节点创建失败: post-store failure',
            'node_id': 42,
        },
    )


@patch('src.services.agent_run_bohrium.get_bohrium_nodes_table')
@patch('src.services.agent_run_bohrium.get_bohrium_node_service')
def test_cleanup_restores_when_ssh_attached_false(
    _mock_node_svc: MagicMock,
    _mock_nodes_table: MagicMock,
) -> None:
    """cleanup_bohrium_after_run restores session/_owns_session from runtime when ssh_attached=False."""
    original_session = MagicMock()
    original_session.is_open = True
    ssh_session = MagicMock()
    ssh_session.is_open = True

    pg = SimpleNamespace(session=ssh_session, _owns_session=False)

    SESSIONS['sess-x'] = {
        'bohrium_runtime': {
            'original_session': original_session,
            'original_owns_session': True,
            'ssh_session': ssh_session,
        },
        'bohrium_node_id': None,
    }

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = None
    sessions_service.get_session_user_id.return_value = None

    svc = _make_bohrium_service(sessions_service)
    svc._cleanup_bohrium_after_run(
        session_id='sess-x',
        event_callback=MagicMock(),
        pg_for_run=pg,
        ssh_attached=False,
    )

    assert pg.session is original_session
    assert pg._owns_session is True
    ssh_session.close.assert_called_once()
    assert 'bohrium_runtime' not in SESSIONS['sess-x']


def test_setup_with_required_bohrium_can_continue_after_retry_success() -> None:
    from src.services.user_service import BohriumAccessKeyFetchResult

    svc = _make_bohrium_service()
    expected = BohriumSetupResult(
        True,
        None,
        MagicMock(),
        '/share',
        'ssh',
        None,
    )
    access_key_result = BohriumAccessKeyFetchResult(
        status='success',
        access_key='ak',
        retryable=False,
        attempts=2,
    )

    with (
        patch.object(
            svc,
            '_load_run_credentials',
            return_value=({'project_id': 99}, 'u1', 'o1'),
        ),
        patch.object(svc, '_make_event_bridge', return_value=MagicMock()),
        patch.object(
            svc, '_setup_bohrium_for_run', return_value=expected
        ) as mock_setup,
        patch(
            'src.services.agent_run_bohrium.UserService.fetch_bohrium_access_key_result',
            return_value=access_key_result,
        ),
    ):
        result = asyncio.run(
            svc.run_setup(
                session_id='sess-ok',
                playground=MagicMock(),
                run_started_at=1.0,
                bohrium_required=True,
            )
        )

    assert result is expected
    assert mock_setup.call_args.kwargs['run_creds']['access_key'] == 'ak'


def _make_no_attach_bohrium_result() -> MagicMock:
    """Bohrium mock with explicit None execution fields (bare MagicMock is truthy)."""
    r = MagicMock()
    r.ssh_attached = False
    r.abort_result = None
    r.execution_session = None
    r.execution_workdir = None
    r.session_type = None
    r._asdict.return_value = {
        'ssh_attached': False,
        'abort_result': None,
        'execution_session': None,
        'execution_workdir': None,
        'session_type': None,
    }
    return r


@patch('matmaster.providers.llm_factory.build_provider')
@patch('matmaster.config.loader.load_llm_config')
def test_run_agent_loads_exp_config_without_passing_skill_sync_to_bohrium_setup(
    mock_load_llm: MagicMock,
    mock_build_provider: MagicMock,
    tmp_path: Path,
) -> None:
    """run_agent loads Exp config but does not own skill directory sync."""
    AgentRunService = pytest.importorskip(
        "src.services.agent_run_service",
        reason="src not available (isolation test)",
    ).AgentRunService

    order: list[str] = []
    _real_load_exp = matmaster_loader.load_exp_config

    def tracked_load_exp(name: str, **kwargs: Any) -> Any:
        order.append('load_exp_config')
        return _real_load_exp(name, **kwargs)

    mock_sessions_svc = MagicMock()
    mock_sessions_svc.get_session_user_id.return_value = 'user-123'
    svc = AgentRunService(sessions_service=mock_sessions_svc)

    mock_pg = MagicMock()
    mock_pg_ctx = PlaygroundContext(
        workdir=tmp_path / 'workspace',
        session_type='local',
        cache_area=tmp_path / 'cache',
        run_meta={'run_dir': str(tmp_path), 'task_id': 'test-task'},
    )
    mock_pg.prepare.return_value = mock_pg_ctx
    mock_pg.config_path = Path('config/config.yaml')
    mock_pg.session = None
    captured_setup_kwargs: dict[str, Any] = {}
    mock_bohrium_result = _make_no_attach_bohrium_result()

    def tracked_setup(**kwargs: Any) -> MagicMock:
        order.append('setup')
        captured_setup_kwargs.update(kwargs)
        return mock_bohrium_result

    mock_llm = MockLLMProvider()
    mock_build_provider.return_value = mock_llm
    mock_load_llm.return_value = MagicMock()

    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch('src.services.agent_run_bohrium_stage.BohriumSetupService') as mock_bohrium_cls,
        patch('src.services.agent_run_service.get_chat_events_table') as mock_events_fn,
        patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
        patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        patch.object(
            matmaster_loader,
            'load_exp_config',
            side_effect=tracked_load_exp,
        ),
    ):
        mock_bohrium_svc = mock_bohrium_cls.return_value

        async def _async_tracked_setup(**kwargs: Any) -> MagicMock:
            return tracked_setup(**kwargs)

        mock_bohrium_svc.run_setup = AsyncMock(side_effect=_async_tracked_setup)
        mock_bohrium_svc.run_cleanup = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.get_session_events.return_value = []
        mock_events_fn.return_value = mock_events_table
        mock_redis_fn.return_value = MagicMock()

        async def _mock_use_quota(uid: str, **_: Any) -> None:
            pass

        mock_use_quota.side_effect = _mock_use_quota

        asyncio.run(
            svc.run_agent(
                session_id='sess-spec-order',
                user_prompt='prompt',
                send_cb=AsyncMock(),
                cancel_token=CancellationController().token,
                mode='direct',
                task_id='task-spec-order',
            )
        )

    assert order.index('load_exp_config') < order.index('setup')
    assert 'skill_sync_spec' not in captured_setup_kwargs


@patch('matmaster.providers.llm_factory.build_provider')
@patch('matmaster.config.loader.load_llm_config')
def test_execution_binding_before_build_runtime(
    mock_load_llm: MagicMock,
    mock_build_provider: MagicMock,
    tmp_path: Path,
) -> None:
    """When Bohrium returns an execution binding, pg_ctx passed to Exp.build_runtime is updated."""
    AgentRunService = pytest.importorskip(
        "src.services.agent_run_service",
        reason="src not available (isolation test)",
    ).AgentRunService

    mock_sessions_svc = MagicMock()
    mock_sessions_svc.get_session_user_id.return_value = 'user-123'
    svc = AgentRunService(sessions_service=mock_sessions_svc)

    mock_pg = MagicMock()
    mock_pg_ctx = PlaygroundContext(
        workdir=tmp_path / 'workspace',
        session_type='local',
        cache_area=tmp_path / 'cache',
        run_meta={'run_dir': str(tmp_path), 'task_id': 'test-task'},
    )
    mock_pg.prepare.return_value = mock_pg_ctx
    mock_pg.config_path = Path('config/config.yaml')
    mock_pg.session = None

    mock_exec = MagicMock()
    mock_bohrium_result = BohriumSetupResult(
        True,
        None,
        mock_exec,
        '/remote/ws',
        'ssh',
        BohriumRuntimeSnapshot(
            session_type='ssh',
            execution_workdir='/remote/ws',
            remote_workspace_root='/share',
            remote_project_root='/share/.matmaster',
            node_id=9,
            node_ip='10.0.0.9',
            ssh_attached=True,
        ),
    )

    mock_llm = MagicMock()
    mock_build_provider.return_value = mock_llm
    mock_load_llm.return_value = MagicMock()

    from matmaster.types.events import RunResultEvent

    mock_run_result_event = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
    )

    captured_run_stream_args: dict[str, Any] = {}

    async def _mock_run_stream(*args, **kwargs):
        captured_run_stream_args['ctx'] = args[0] if args else None
        captured_run_stream_args['kwargs'] = kwargs
        yield mock_run_result_event

    mock_exp_inst = MagicMock()
    mock_exp_inst.run_stream = _mock_run_stream
    mock_exp_inst._run_cleanup_callbacks = AsyncMock()

    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch('src.services.agent_run_bohrium_stage.BohriumSetupService') as mock_bohrium_cls,
        patch('src.services.agent_run_service.get_chat_events_table') as mock_events_fn,
        patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
        patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        patch('matmaster.core.exp.Exp', return_value=mock_exp_inst),
    ):
        mock_bohrium_svc = mock_bohrium_cls.return_value
        mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
        mock_bohrium_svc.run_cleanup = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.get_session_events.return_value = []
        mock_events_fn.return_value = mock_events_table
        mock_redis_fn.return_value = MagicMock()

        async def _mock_use_quota(uid: str) -> None:
            pass

        mock_use_quota.side_effect = _mock_use_quota

        asyncio.run(
            svc.run_agent(
                session_id='sess-exec-bind',
                user_prompt='prompt',
                send_cb=AsyncMock(),
                cancel_token=CancellationController().token,
                mode='direct',
                task_id='task-exec-bind',
            )
        )

    pg_passed = captured_run_stream_args['ctx']
    assert pg_passed.session is mock_exec
    assert pg_passed.session_type == 'ssh'
    assert pg_passed.execution_workdir == '/remote/ws'
    bmeta = pg_passed.run_meta.get('bohrium', {})
    assert 'execution_session' not in bmeta
    assert bmeta.get('ssh_attached') is True
    assert bmeta.get('execution_workdir') == '/remote/ws'
    assert bmeta.get('session_type') == 'ssh'


@patch.object(arb, "_run_clear_remote_proxy", MagicMock())
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_setup_uses_remote_workdir_for_ssh_and_execution_context(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    node_svc = MagicMock()
    mock_node_svc_factory.return_value = node_svc
    nodes_table = MagicMock()
    mock_nodes_table_factory.return_value = nodes_table
    nodes_table.find_one_for_reuse.return_value = None
    nodes_table.list_node_ids_for_user_org.return_value = []
    node_svc.create_node.return_value = {"node_id": 42}
    node_svc.wait_until_ready.return_value = {
        "ip": "10.0.0.1",
        "password": "secret",
    }

    original_session = MagicMock()
    original_session.is_open = True
    pg = _make_pg(original_session)
    mock_ssh = MagicMock()
    mock_ssh.is_open = True
    mock_ssh.remote_project_root = "/remote/proj"

    with patch.object(arb, "SSHSession", return_value=mock_ssh) as mock_ssh_cls:
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id="sess-dir",
            pg=pg,
            run_creds={"access_key": "ak", "project_id": 99},
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=MagicMock(),
            run_started_at=0.0,
            remote_workdir="/share/case",
        )

    cfg = mock_ssh_cls.call_args.args[0]
    assert cfg.working_dir == "/share/case"
    assert cfg.workspace_path == "/share/case"
    assert result.execution_workdir == "/share/case"
    assert result.runtime_snapshot is not None
    assert result.runtime_snapshot.execution_workdir == "/share/case"
    assert result.runtime_snapshot.remote_workspace_root == "/share"
    assert result.runtime_snapshot.remote_project_root == "/remote/proj"
    mock_ssh.open.assert_called_once()


def test_run_setup_forwards_remote_workdir_to_setup() -> None:
    from src.services.user_service import BohriumAccessKeyFetchResult

    svc = _make_bohrium_service()
    expected = BohriumSetupResult(True, None, MagicMock(), "/share/case", "ssh", None)
    access_key_result = BohriumAccessKeyFetchResult(
        status="success",
        access_key="ak",
        retryable=False,
        attempts=1,
    )

    with (
        patch.object(
            svc,
            "_load_run_credentials",
            return_value=({"project_id": 99}, "u1", "o1"),
        ),
        patch.object(svc, "_make_event_bridge", return_value=MagicMock()),
        patch.object(
            svc, "_setup_bohrium_for_run", return_value=expected
        ) as mock_setup,
        patch(
            "src.services.agent_run_bohrium.UserService.fetch_bohrium_access_key_result",
            return_value=access_key_result,
        ),
    ):
        result = asyncio.run(
            svc.run_setup(
                session_id="sess-dir",
                playground=MagicMock(),
                run_started_at=1.0,
                bohrium_required=True,
                remote_workdir="/share/case",
            )
        )

    assert result is expected
    assert mock_setup.call_args.kwargs["remote_workdir"] == "/share/case"
