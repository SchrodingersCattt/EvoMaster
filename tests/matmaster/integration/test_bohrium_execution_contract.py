"""Focused tests for Bohrium SSH setup/cleanup contract (runtime state, no mixin helpers)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import matmaster.config.loader as matmaster_loader
from matmaster.integration.bohrium_setup import SkillSyncSpec
from matmaster.types.context import PlaygroundContext
from tests.matmaster.core.conftest import MockLLMProvider

_src_services = pytest.importorskip(
    "src.services.agent_run_bohrium",
    reason="src not available (isolation test)",
)
arb = _src_services
BohriumSetupResult = _src_services.BohriumSetupResult
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


@patch.object(arb, '_sync_skills_to_ssh_session', MagicMock())
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

    with patch.object(arb, 'SSHSession', return_value=mock_ssh) as mock_ssh_cls:
        result = arb.setup_bohrium_for_run(
            session_id='sess-ok',
            pg=pg,
            skill_sync_spec=SkillSyncSpec(
                project_skill_roots=['/tmp/proj_skills'],
                remote_project_root='/remote/proj',
            ),
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
def test_setup_skips_skills_synced_event_when_skill_sync_returns_false(
    mock_node_svc_factory: MagicMock,
    mock_nodes_table_factory: MagicMock,
    mock_remote_workspace_root: MagicMock,
) -> None:
    """No skills_synced telemetry should be emitted when skill sync is skipped."""
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
        result = arb.setup_bohrium_for_run(
            session_id='sess-no-skill-sync',
            pg=pg,
            skill_sync_spec=None,
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

    with (
        patch.object(arb, 'SSHSession', return_value=mock_ssh),
        patch.object(arb, '_sync_skills_to_ssh_session', MagicMock()),
    ):
        mock_run_clear_remote_proxy.side_effect = _raise_after_store
        event_callback = MagicMock()
        result = arb.setup_bohrium_for_run(
            session_id='sess-fail',
            pg=pg,
            skill_sync_spec=None,
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

    arb.cleanup_bohrium_after_run(
        session_id='sess-x',
        sessions_service=sessions_service,
        event_callback=MagicMock(),
        pg_for_run=pg,
        ssh_attached=False,
    )

    assert pg.session is original_session
    assert pg._owns_session is True
    ssh_session.close.assert_called_once()
    assert 'bohrium_runtime' not in SESSIONS['sess-x']


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
def test_skill_sync_spec_load_exp_config_before_bohrium_setup(
    mock_load_llm: MagicMock,
    mock_build_provider: MagicMock,
    tmp_path: Path,
) -> None:
    """load_exp_config runs before Bohrium setup; derived SkillSyncSpec is passed to setup."""
    AgentRunService = pytest.importorskip(
        "src.services.agent_run_service",
        reason="src not available (isolation test)",
    ).AgentRunService

    order: list[str] = []
    _real_load_exp = matmaster_loader.load_exp_config

    def tracked_load_exp(name: str) -> Any:
        order.append('load_exp_config')
        return _real_load_exp(name)

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
    captured_spec: dict[str, Any] = {}
    mock_bohrium_result = _make_no_attach_bohrium_result()

    def tracked_setup(**kwargs: Any) -> MagicMock:
        order.append('setup')
        captured_spec['skill_sync_spec'] = kwargs.get('skill_sync_spec')
        return mock_bohrium_result

    mock_llm = MockLLMProvider()
    mock_build_provider.return_value = mock_llm
    mock_load_llm.return_value = MagicMock()

    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch('src.services.agent_run_service.BohriumSetupService') as mock_bohrium_cls,
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

        async def _mock_use_quota(uid: str) -> None:
            pass

        mock_use_quota.side_effect = _mock_use_quota

        asyncio.run(
            svc.run_agent(
                session_id='sess-spec-order',
                user_prompt='prompt',
                send_cb=AsyncMock(),
                stop_event=threading.Event(),
                mode='direct',
                reply_queue=None,
                task_id='task-spec-order',
            )
        )

    assert order.index('load_exp_config') < order.index('setup')
    spec = captured_spec.get('skill_sync_spec')
    assert spec is not None
    assert isinstance(spec, SkillSyncSpec)
    assert spec.remote_project_root == '/share/.matmaster'
    assert spec.project_skill_roots
    assert spec.project_skill_roots[0].endswith(
        str(Path('matmaster/skills/lazymcp'))
    )


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
    )

    mock_llm = MagicMock()
    mock_build_provider.return_value = mock_llm
    mock_load_llm.return_value = MagicMock()

    mock_runtime = MagicMock()
    mock_runtime.spec = MagicMock()
    mock_runtime.spec.hooks = []
    mock_runtime.spec.tool_registry = None
    mock_runtime.spec.model_copy.return_value = mock_runtime.spec
    mock_kernel_result = MagicMock()
    mock_run_evt = MagicMock()
    mock_run_evt.reason = 'natural'
    mock_run_evt.status = 'completed'
    mock_run_evt.final_content = None
    mock_run_evt.source = 'MatMaster'
    mock_kernel_result.result.to_run_result_event.return_value = mock_run_evt
    mock_runtime.kernel.run = AsyncMock(return_value=mock_kernel_result)

    mock_exp_inst = MagicMock()
    mock_exp_inst.build_runtime = AsyncMock(return_value=mock_runtime)
    mock_exp_inst._run_cleanup_callbacks = AsyncMock()

    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch('src.services.agent_run_service.BohriumSetupService') as mock_bohrium_cls,
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
                stop_event=threading.Event(),
                mode='direct',
                reply_queue=None,
                task_id='task-exec-bind',
            )
        )

    pg_passed = mock_exp_inst.build_runtime.call_args[0][0]
    assert pg_passed.session is mock_exec
    assert pg_passed.session_type == 'ssh'
    assert pg_passed.execution_workdir == '/remote/ws'
    bmeta = pg_passed.run_meta.get('bohrium', {})
    assert 'execution_session' not in bmeta
    assert bmeta.get('ssh_attached') is True
    assert bmeta.get('execution_workdir') == '/remote/ws'
    assert bmeta.get('session_type') == 'ssh'


def test_skill_sync_upload_exclude_set_does_not_exclude_skill_md(
    tmp_path: Path,
) -> None:
    """Skill tree upload must not exclude SKILL.md (contract files sync to the node)."""
    SSHSession = pytest.importorskip(
        "evomaster.agent.session.ssh",
        reason="evomaster not available (isolation test)",
    ).SSHSession

    root = tmp_path / 'proj_skills'
    root.mkdir()
    spec = SkillSyncSpec(
        project_skill_roots=[str(root)],
        remote_project_root='/remote/proj',
    )
    ssh = SSHSession()
    excludes: list[set[str]] = []

    def _capture_upload(
        _env: Any, _local_dir: str, _remote_dir: str, exclude: set[str] | None = None
    ) -> None:
        excludes.append(set(exclude) if exclude is not None else set())

    with patch.object(arb, '_upload_directory', side_effect=_capture_upload):
        arb._sync_skills_to_ssh_session(ssh, spec, pg=MagicMock())

    assert excludes
    assert all('SKILL.md' not in ex for ex in excludes)
