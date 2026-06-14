from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports


def test_record_submit_passes_identity_snapshot() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="u1",
        org_id="o1",
        spawn_id="sp-1",
        workspace="/share/project/../project",
        table=table,
    )
    ledger.record_submit(
        job_id="12345",
        job_name="j",
        project_id=42,
        sandbox=True,
        input_dir="data/in",
    )
    table.insert_submitted.assert_called_once()
    kw = table.insert_submitted.call_args.kwargs
    assert kw["session_id"] == "sess-1"
    assert kw["invocation_id"] == "inv-1"
    assert kw["spawn_id"] == "sp-1"
    assert kw["user_id"] == "u1"
    assert kw["org_id"] == "o1"
    assert kw["job_id"] == "12345"
    assert kw["sandbox"] is True
    assert kw["input_dir"] == "data/in"
    assert kw["workspace"] == "/share/project"


def test_record_submit_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="",
        org_id="o1",
        workspace="/share/project",
        table=table,
    )
    with pytest.raises(ValueError):
        ledger.record_submit(
            job_id="1",
            job_name=None,
            project_id=1,
            sandbox=False,
            input_dir="data/in",
        )
    table.insert_submitted.assert_not_called()


def test_record_submit_allows_null_invocation_id() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id=None,
        user_id="u1",
        org_id="o1",
        workspace="/share/project",
        table=table,
    )
    ledger.record_submit(
        job_id="1",
        job_name=None,
        project_id=1,
        sandbox=False,
        input_dir="data/in",
    )
    assert table.insert_submitted.call_args.kwargs["invocation_id"] is None


def test_ledger_write_port_is_none_without_workspace() -> None:
    table = MagicMock()
    ledger, jobs = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="u1",
        org_id="o1",
        workspace=None,
        table=table,
    )

    assert ledger is None
    assert jobs is not None
    table.insert_submitted.assert_not_called()


def test_ledger_workspace_must_be_share_path() -> None:
    table = MagicMock()
    with pytest.raises(ValueError, match="bohrium ledger workspace"):
        build_bohrium_jobs_ports(
            session_id="sess-1",
            invocation_id="inv-1",
            user_id="u1",
            org_id="o1",
            workspace="/tmp/project",
            table=table,
        )


def test_record_poll_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="",
        org_id="o1",
        workspace="/share/project",
        table=table,
    )
    with pytest.raises(ValueError):
        ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    table.apply_poll.assert_not_called()


def test_record_poll_normalizes_status_code() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        table=table,
    )
    ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    kw = table.apply_poll.call_args.kwargs
    assert kw["status"] == "finished"
    assert kw["is_terminal"] is True


@pytest.mark.asyncio
async def test_jobs_port_without_snapshot_renders_empty_pending() -> None:
    # 渲染统一为冻结语义：无 snapshot（身份降级）→ pending 恒空，active 仍实时。
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        table=table,
    )
    from matmaster.context.ports import WorkspaceJobsQuery

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result.active_jobs == ({"job_id": "a"},)
    assert result.pending_terminal_jobs == ()
    assert result.detail_limit is None


def test_ports_do_not_construct_table_until_identity_allows_use() -> None:
    table_factory = MagicMock(side_effect=AssertionError("should stay lazy"))
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="",
        workspace="/share/project",
        table_factory=table_factory,
    )

    with pytest.raises(ValueError):
        ledger.record_submit(
            job_id="1",
            job_name=None,
            project_id=1,
            sandbox=False,
            input_dir="data/in",
        )

    assert table_factory.call_count == 0


def test_session_identity_resolution_helper_uses_session_snapshot() -> None:
    from src.services import agent_run_service as ars

    captured = {}

    class _FakeSessions:
        def get_session(self, sid):
            captured["sid"] = sid
            return {"user_id": "user-from-db", "org_id": "org-from-db"}

    user, org = ars._resolve_session_identity("sess-1", sessions_source=_FakeSessions())
    assert user == "user-from-db"
    assert org == "org-from-db"
    assert captured["sid"] == "sess-1"


def test_session_identity_resolution_prefers_explicit_run_user_id() -> None:
    from src.services import agent_run_service as ars

    class _FakeSessions:
        def get_session(self, sid):
            return {"user_id": "user-from-db", "org_id": "org-from-db"}

    user, org = ars._resolve_session_identity(
        "sess-1", user_id="user-from-run", sessions_source=_FakeSessions()
    )
    assert user == "user-from-run"
    assert org == "org-from-db"


def _snapshot(rows):
    from src.services.bohrium_delivery_ack import DeliverySnapshot

    return DeliverySnapshot(
        user_id="u",
        org_id="o",
        session_id="s",
        workspace="/share/project",
        rows=tuple(rows),
        detail_limit=20,
    )


@pytest.mark.asyncio
async def test_jobs_port_serves_pending_from_snapshot_with_detail_limit() -> None:
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    snap_rows = [
        {"id": 2, "job_id": "f1", "status": "failed"},
        {"id": 1, "job_id": "t1", "status": "finished"},
    ]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        table=table,
        delivery_snapshot=_snapshot(snap_rows),
    )
    from matmaster.context.ports import WorkspaceJobsQuery

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    # pending 据 snapshot.rows（失败优先序原样），不再裸查 limit=5 定交付集合
    assert result.pending_terminal_jobs == tuple(snap_rows)
    assert result.detail_limit == 20
    # active 仍走实时查询（snapshot 只钉死 pending）
    assert result.active_jobs == ({"job_id": "a"},)
    table.query_session_active.assert_called_once()


def test_record_poll_terminal_feeds_observed_set() -> None:
    table = MagicMock()
    snap = _snapshot([])
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        table=table,
        delivery_snapshot=snap,
    )
    ledger.record_poll(job_id="J", sandbox=True, status_code=2)
    ledger.record_poll(job_id="K", sandbox=False, status_code=1)
    assert snap.observed_terminal == {(True, "J")}


def test_record_poll_without_snapshot_skips_observation() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        table=table,
    )
    ledger.record_poll(job_id="J", sandbox=False, status_code=2)
    table.apply_poll.assert_called_once()
