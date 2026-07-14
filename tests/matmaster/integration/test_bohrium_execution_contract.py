"""Focused tests for Bohrium SSH setup/cleanup contract (runtime state, no mixin helpers)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

import matmaster.config.loader as matmaster_loader
from matmaster.bohrium.types import BohriumRuntimeSnapshot
from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.cancellation import CancellationController
from matmaster.types.run_metadata import RunMetadata
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
        "skills": {"skills_root": "matmaster/skills"},
    }
    return pg


def _make_bohrium_service(sessions_service: Any | None = None) -> BohriumSetupService:
    return BohriumSetupService(
        sessions_service=sessions_service or MagicMock(),
        event_sink=lambda event: None,
    )


def _allow_user_turn_context_write(events_table: MagicMock) -> None:
    events_table.get_history_checkpoints.return_value = []
    events_table.has_user_turn_context.return_value = False
    events_table.get_session_user_query_events.return_value = []
    events_table.query_context_events.return_value = []
    events_table.get_recent_context_anchor_events.return_value = []
    events_table.query_user_turn_context_by_invocation.return_value = None
    events_table.add_event.return_value = True


@patch.object(arb, "_run_clear_remote_proxy", MagicMock())
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
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
    mock_ssh._env = MagicMock()
    mock_ssh._env.upload_directory_tarball = MagicMock(return_value=1)
    mock_ssh.remote_project_root = "/remote/proj"

    event_callback = MagicMock()
    with patch.object(arb, "SSHSession", return_value=mock_ssh) as mock_ssh_cls:
        svc = _make_bohrium_service()
        result = svc._setup_bohrium_for_run(
            session_id="sess-ok",
            pg=pg,
            run_creds={
                "access_key": "ak",
                "project_id": 99,
            },
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=event_callback,
            run_started_at=0.0,
            bohrium_node_sku_id=12345,
        )

    assert result.ssh_attached is True
    assert result.abort_result is None
    assert result.execution_session is mock_ssh
    assert result.execution_workdir == "/share"
    assert result.session_type == "ssh"

    assert pg.session is mock_ssh
    assert pg._owns_session is False

    mock_ssh_cls.assert_called_once()
    cfg = mock_ssh_cls.call_args[0][0]
    assert cfg.host == "10.0.0.1"
    assert cfg.password == "secret"
    assert cfg.workspace_path == "/share"

    mock_ssh.open.assert_called_once()

    rt = SESSIONS["sess-ok"].get("bohrium_runtime")
    assert rt is not None
    assert rt["original_session"] is original_session
    assert rt["original_owns_session"] is True
    assert rt["ssh_session"] is mock_ssh
    nodes_table.find_one_for_reuse.assert_called_once_with("u1", "o1", 99, 12345)
    node_svc.create_node.assert_called_once_with("ak", 99, sku_id=12345)
    nodes_table.insert_node.assert_called_once_with("u1", "o1", 99, 12345, 42)
    assert SESSIONS["sess-ok"]["bohrium_node_reuse_tracked"] is True
    statuses = [
        call.args[2]["status"]
        for call in event_callback.call_args_list
        if call.args[1] == "bohrium_node"
    ]
    assert statuses == [
        "acquiring",
        "creating",
        "starting",
        "ready",
        "connecting",
        "connected",
    ]


def test_invocation_setup_uses_fenced_lease_and_cleanup_releases_it() -> None:
    from src.services.bohrium_node_lifecycle import NodeIdentity, NodeLease

    identity = NodeIdentity("u1", "o1", 99, 12345)
    lease = NodeLease(
        identity=identity,
        node_slot_id=7,
        node_id=42,
        session_id="sess-lease",
        invocation_id="inv-1",
        lease_token="token-1",
        ip="10.0.0.1",
        password="secret",
    )
    manager = MagicMock()

    def acquire_with_progress(*_args: Any, **kwargs: Any) -> Any:
        report = kwargs["progress_reporter"]
        report("creating", None, "正在创建 Bohrium 计算节点...")
        report("starting", 42, "节点已创建，正在等待资源就绪...")
        return lease

    manager.acquire.side_effect = acquire_with_progress
    heartbeat = MagicMock()
    original_session = MagicMock(is_open=True)
    pg = _make_pg(original_session)
    ssh = MagicMock(is_open=True, remote_project_root="/remote/proj")
    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {
        "user_id": "u1",
        "org_id": "o1",
        "project_id": 99,
    }

    with (
        patch.object(arb, "SSHSession", return_value=ssh),
        patch.object(arb, "_run_clear_remote_proxy", MagicMock()),
        patch.object(arb, "_remote_session_workspace_root", return_value="/share"),
        patch.object(arb, "get_bohrium_node_lease_manager", return_value=manager),
        patch.object(arb, "NodeLeaseHeartbeat", return_value=heartbeat),
        patch.object(arb.UserService, "get_bohrium_access_key", return_value="ak"),
    ):
        event_callback = MagicMock()
        svc = _make_bohrium_service(sessions_service)
        result = svc._setup_bohrium_for_run(
            session_id="sess-lease",
            pg=pg,
            run_creds={"access_key": "ak", "project_id": 99},
            user_id_for_ak="u1",
            org_id="o1",
            event_callback=event_callback,
            run_started_at=0.0,
            bohrium_node_sku_id=12345,
            invocation_id="inv-1",
            bohrium_node_lifecycle_policy="idle_timeout",
            bohrium_node_idle_timeout_seconds=1800,
        )

        assert result.ssh_attached is True
        manager.acquire.assert_called_once_with(
            identity,
            session_id="sess-lease",
            invocation_id="inv-1",
            access_key="ak",
            creator_id=arb._creator_id_from_user("u1"),
            lifecycle_policy="idle_timeout",
            idle_timeout_seconds=1800,
            progress_reporter=ANY,
        )
        heartbeat.start.assert_called_once_with()
        statuses = [
            call.args[2]["status"]
            for call in event_callback.call_args_list
            if call.args[1] == "bohrium_node"
        ]
        assert statuses == [
            "acquiring",
            "creating",
            "starting",
            "ready",
            "connecting",
            "connected",
        ]

        svc._cleanup_bohrium_after_run(
            session_id="sess-lease",
            event_callback=MagicMock(),
            pg_for_run=pg,
            ssh_attached=True,
            invocation_id="inv-1",
        )

    heartbeat.stop.assert_called_once_with()
    manager.release.assert_called_once_with(
        lease,
        access_key="ak",
        creator_id=arb._creator_id_from_user("u1"),
    )


def test_setup_with_required_bohrium_can_continue_after_retry_success() -> None:
    from src.services.user_service import BohriumAccessKeyFetchResult

    svc = _make_bohrium_service()
    expected = BohriumSetupResult(
        True,
        None,
        MagicMock(),
        "/share",
        "ssh",
        None,
    )
    access_key_result = BohriumAccessKeyFetchResult(
        status="success",
        access_key="ak",
        retryable=False,
        attempts=2,
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
                session_id="sess-ok",
                playground=MagicMock(),
                run_started_at=1.0,
                bohrium_required=True,
            )
        )

    assert result is expected
    assert mock_setup.call_args.kwargs["run_creds"]["access_key"] == "ak"


def _make_no_attach_bohrium_result() -> MagicMock:
    """Bohrium mock with explicit None execution fields (bare MagicMock is truthy)."""
    r = MagicMock()
    r.ssh_attached = False
    r.abort_result = None
    r.execution_session = None
    r.execution_workdir = None
    r.session_type = None
    r._asdict.return_value = {
        "ssh_attached": False,
        "abort_result": None,
        "execution_session": None,
        "execution_workdir": None,
        "session_type": None,
    }
    return r


def _provider_bundle(provider: Any) -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        model="test-model",
        model_profile="test-profile",
        model_route="test-route",
        provider_name="openai",
        context_limit=345_000,
        context_limit_source="profile",
        supports_vision=False,
        vision_detail=None,
    )


@patch("matmaster.providers.llm_factory.build_provider_bundle")
@patch("matmaster.config.loader.load_llm_config")
def test_run_agent_loads_exp_config_without_passing_skill_sync_to_bohrium_setup(
    mock_load_llm: MagicMock,
    mock_build_provider_bundle: MagicMock,
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
        order.append("load_exp_config")
        return _real_load_exp(name, **kwargs)

    mock_sessions_svc = MagicMock()
    mock_sessions_svc.get_session_user_id.return_value = "user-123"
    svc = AgentRunService(sessions_service=mock_sessions_svc)

    mock_pg = MagicMock()
    mock_pg_env = ExecutionEnvironment(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        metadata=RunMetadata(run_dir=str(tmp_path), task_id="test-task"),
    )
    mock_pg.prepare.return_value = mock_pg_env
    mock_pg.config_path = Path("config/config.yaml")
    mock_pg.session = None
    captured_setup_kwargs: dict[str, Any] = {}
    mock_bohrium_result = _make_no_attach_bohrium_result()

    def tracked_setup(**kwargs: Any) -> MagicMock:
        order.append("setup")
        captured_setup_kwargs.update(kwargs)
        return mock_bohrium_result

    mock_llm = MockLLMProvider()
    mock_build_provider_bundle.return_value = _provider_bundle(mock_llm)
    mock_load_llm.return_value = MagicMock()

    with (
        patch.object(svc._pg_manager, "get_or_create", return_value=mock_pg),
        patch(
            "src.services.agent_run_bohrium_stage.BohriumSetupService"
        ) as mock_bohrium_cls,
        patch("src.services.agent_run_service.get_chat_events_table") as mock_events_fn,
        patch("src.services.agent_run_service.get_redis_dao") as mock_redis_fn,
        patch.object(
            matmaster_loader,
            "load_exp_config",
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
        _allow_user_turn_context_write(mock_events_table)
        mock_events_fn.return_value = mock_events_table
        mock_redis_fn.return_value = MagicMock()

        asyncio.run(
            svc.run_agent(
                session_id="sess-spec-order",
                user_prompt="prompt",
                send_cb=AsyncMock(),
                cancel_token=CancellationController().token,
                mode="direct",
                task_id="task-spec-order",
                invocation_id="inv-spec-order",
            )
        )

    assert order.index("load_exp_config") < order.index("setup")
    assert "skill_sync_spec" not in captured_setup_kwargs


@patch("matmaster.providers.llm_factory.build_provider_bundle")
@patch("matmaster.config.loader.load_llm_config")
def test_execution_binding_before_build_runtime(
    mock_load_llm: MagicMock,
    mock_build_provider_bundle: MagicMock,
    tmp_path: Path,
) -> None:
    """When Bohrium returns an execution binding, agent_run_ctx passed to Exp.build_runtime is updated."""
    AgentRunService = pytest.importorskip(
        "src.services.agent_run_service",
        reason="src not available (isolation test)",
    ).AgentRunService

    mock_sessions_svc = MagicMock()
    mock_sessions_svc.get_session_user_id.return_value = "user-123"
    svc = AgentRunService(sessions_service=mock_sessions_svc)

    mock_pg = MagicMock()
    mock_pg_env = ExecutionEnvironment(
        workdir=tmp_path / "workspace",
        session_type="local",
        cache_area=tmp_path / "cache",
        metadata=RunMetadata(run_dir=str(tmp_path), task_id="test-task"),
    )
    mock_pg.prepare.return_value = mock_pg_env
    mock_pg.config_path = Path("config/config.yaml")
    mock_pg.session = None

    mock_exec = MagicMock()
    mock_bohrium_result = BohriumSetupResult(
        True,
        None,
        mock_exec,
        "/share/remote/ws",
        "ssh",
        BohriumRuntimeSnapshot(
            session_type="ssh",
            execution_workdir="/share/remote/ws",
            remote_workspace_root="/share",
            remote_project_root="/share/.matmaster",
            node_id=9,
            node_ip="10.0.0.9",
            ssh_attached=True,
        ),
    )

    mock_llm = MagicMock()
    mock_build_provider_bundle.return_value = _provider_bundle(mock_llm)
    mock_load_llm.return_value = MagicMock()

    from matmaster.types.events import RunResultEvent

    mock_run_result_event = RunResultEvent(
        source="MatMaster",
        status="completed",
        reason="natural",
    )

    captured_run_stream_args: dict[str, Any] = {}

    async def _mock_run_stream(*args, **kwargs):
        captured_run_stream_args["ctx"] = args[0] if args else None
        captured_run_stream_args["kwargs"] = kwargs
        yield mock_run_result_event

    mock_exp_inst = MagicMock()
    mock_exp_inst.run_stream = _mock_run_stream
    mock_exp_inst._run_cleanup_callbacks = AsyncMock()

    with (
        patch.object(svc._pg_manager, "get_or_create", return_value=mock_pg),
        patch(
            "src.services.agent_run_bohrium_stage.BohriumSetupService"
        ) as mock_bohrium_cls,
        patch("src.services.agent_run_service.get_chat_events_table") as mock_events_fn,
        patch("src.services.agent_run_service.get_redis_dao") as mock_redis_fn,
        patch("matmaster.core.exp.Exp", return_value=mock_exp_inst),
    ):
        mock_bohrium_svc = mock_bohrium_cls.return_value
        mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
        mock_bohrium_svc.run_cleanup = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.get_session_events.return_value = []
        _allow_user_turn_context_write(mock_events_table)
        mock_events_fn.return_value = mock_events_table
        mock_redis_fn.return_value = MagicMock()

        asyncio.run(
            svc.run_agent(
                session_id="sess-exec-bind",
                user_prompt="prompt",
                send_cb=AsyncMock(),
                cancel_token=CancellationController().token,
                mode="direct",
                task_id="task-exec-bind",
                invocation_id="inv-exec-bind",
            )
        )

    ctx_passed = captured_run_stream_args["ctx"]
    assert ctx_passed.environment.session is mock_exec
    assert ctx_passed.environment.session_type == "ssh"
    assert ctx_passed.environment.execution_workdir == "/share/remote/ws"
    snapshot = ctx_passed.environment.bohrium.snapshot
    assert snapshot is not None
    assert snapshot.ssh_attached is True
    assert snapshot.remote_workspace_root == "/share"
    assert snapshot.remote_project_root == "/share/.matmaster"
    assert "bohrium" not in RunMetadata.model_fields


@patch.object(arb, "_run_clear_remote_proxy", MagicMock())
@patch.object(arb, "_remote_session_workspace_root", return_value="/share")
@patch("src.services.agent_run_bohrium.get_bohrium_nodes_table")
@patch("src.services.agent_run_bohrium.get_bohrium_node_service")
def test_setup_uses_workspace_for_ssh_and_execution_context(
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
            workspace="/share/case",
        )

    cfg = mock_ssh_cls.call_args.args[0]
    assert cfg.workspace_path == "/share/case"
    assert result.execution_workdir == "/share/case"
    assert result.runtime_snapshot is not None
    assert result.runtime_snapshot.execution_workdir == "/share/case"
    assert result.runtime_snapshot.remote_workspace_root == "/share"
    assert result.runtime_snapshot.remote_project_root == "/remote/proj"
    mock_ssh.open.assert_called_once()


def test_run_setup_forwards_workspace_to_setup() -> None:
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
                workspace="/share/case",
            )
        )

    assert result is expected
    assert mock_setup.call_args.kwargs["workspace"] == "/share/case"
    assert "remote_workdir" not in mock_setup.call_args.kwargs
