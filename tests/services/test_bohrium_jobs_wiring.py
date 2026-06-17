from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.bohrium_jobs_wiring import build_bohrium_jobs_ports


def _exporter(session=None):
    from src.services.workspace_jobs_export import WorkspaceJobsCsvExporter

    return WorkspaceJobsCsvExporter(
        session=session,
        execution_workdir="/share/project",
        session_id="s",
        invocation_id="inv",
        task_id="t",
    )


def test_record_submit_passes_identity_snapshot() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="u1",
        org_id="o1",
        spawn_id="sp-1",
        workspace="/share/project/../project",
        exporter=_exporter(),
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
        exporter=_exporter(),
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
        exporter=_exporter(),
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
        exporter=_exporter(),
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
            exporter=_exporter(),
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
        exporter=_exporter(),
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
        exporter=_exporter(),
        table=table,
    )
    ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    kw = table.apply_poll.call_args.kwargs
    assert kw["status"] == "finished"
    assert kw["is_terminal"] is True


@pytest.mark.asyncio
async def test_observation_mode_reads_three_groups_cross_session() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = [
        {"job_id": "a", "job_name": "na", "status": "running"}
    ]
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p", "job_name": "np", "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.return_value = [
        {"job_id": "r", "job_name": "nr", "status": "finished"}
    ]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "workspace_observation"
    assert result.active_jobs == (
        {"job_id": "a", "job_name": "na", "status": "running"},
    )
    assert result.unhandled_terminal_jobs == (
        {"job_id": "p", "job_name": "np", "status": "failed"},
    )
    assert result.handled_recent_terminal_jobs == (
        {"job_id": "r", "job_name": "nr", "status": "finished"},
    )
    assert result.summary.total == 3
    assert result.required_truncated is False
    assert result.handled_recent_has_more is False
    assert result.export is None
    # required buckets fetch REQUIRED_FETCH_LIMIT + 1 = 2001; reference HANDLED_RECENT_LIMIT + 1 = 21
    assert table.query_workspace_active.call_args.kwargs["limit"] == 2001
    assert table.query_workspace_unhandled_terminal.call_args.kwargs["limit"] == 2001
    assert table.query_workspace_handled_recent_terminal.call_args.kwargs["limit"] == 21


@pytest.mark.asyncio
async def test_observation_over_preview_limit_exports_and_previews(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(5)
    ]
    table.query_workspace_handled_recent_terminal.return_value = []
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.row_count == 5
    assert result.preview_limit == 2
    assert len(result.preview_rows) == 2
    assert all(r["group"] == "unhandled_terminal" for r in result.preview_rows)
    assert result.omitted_count == 3
    assert result.unhandled_terminal_jobs == ()


@pytest.mark.asyncio
async def test_observation_char_limit_exports_even_when_under_preview_limit(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "50")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p1", "job_name": "n" * 20000, "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.return_value = []
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.reason == "char_limit"
    assert result.preview_limit == 50
    assert len(result.preview_rows) == 1
    assert str(result.preview_rows[0]["job_name"]).endswith("...<truncated>")
    assert result.omitted_count == 0
    from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource

    content = WorkspaceJobsSource.from_jobs(result).to_sections()[0].content
    assert len(content) <= 12000
    assert "...<truncated>" in content


@pytest.mark.asyncio
async def test_observation_export_failure_writes_snapshot_and_error(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(5)
    ]
    table.query_workspace_handled_recent_terminal.return_value = []
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export_error is not None
    assert result.export is None
    assert snap.export_failure["reason"] == "session_missing"


@pytest.mark.asyncio
async def test_observation_handled_recent_limit_is_reference_only(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_HANDLED_RECENT_LIMIT", "2")
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "10")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = []
    table.query_workspace_handled_recent_terminal.return_value = [
        {"job_id": "r0", "job_name": "n0", "status": "finished"},
        {"job_id": "r1", "job_name": "n1", "status": "finished"},
        {"job_id": "r2", "job_name": "n2", "status": "finished"},
    ]
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert [r["job_id"] for r in result.handled_recent_terminal_jobs] == ["r0", "r1"]
    assert result.handled_recent_has_more is True
    assert result.required_truncated is False
    assert snap.required_block == {}
    assert result.export is None


@pytest.mark.asyncio
async def test_observation_handled_recent_query_failure_is_reference_unavailable(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": "p1", "job_name": "n", "status": "failed"}
    ]
    table.query_workspace_handled_recent_terminal.side_effect = RuntimeError(
        "reference query down"
    )
    snap = _snapshot([{"id": 1, "job_id": "p1", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.required_error is None
    assert result.handled_recent_unavailable is True
    assert result.handled_recent_terminal_jobs == ()
    assert snap.required_block == {}


@pytest.mark.asyncio
async def test_observation_required_truncated_writes_required_block(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_REQUIRED_FETCH_LIMIT", "2")
    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "10")
    table = MagicMock()
    table.query_workspace_active.return_value = []
    table.query_workspace_handled_recent_terminal.return_value = []
    # 3 rows returned for limit+1=3 -> original exceeded REQUIRED_FETCH_LIMIT=2
    table.query_workspace_unhandled_terminal.return_value = [
        {"job_id": f"p{i}", "job_name": "n", "status": "failed"} for i in range(3)
    ]
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.required_truncated is True
    assert len(result.unhandled_terminal_jobs) == 2
    assert snap.required_block["reason"] == "required_truncated"
    assert snap.required_block["unhandled_terminal_truncated"] is True


@pytest.mark.asyncio
async def test_observation_required_query_failure_writes_required_block_and_error(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    table.query_workspace_active.side_effect = RuntimeError("db down")
    snap = _snapshot([{"id": 1, "job_id": "p0", "status": "failed"}])
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="workspace_observation",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "workspace_observation"
    assert result.workspace == "/share/project"
    assert result.required_error == {"reason": "query_failed"}
    assert snap.required_block["reason"] == "query_failed"


@pytest.mark.asyncio
async def test_observation_mode_empty_when_workspace_missing() -> None:
    from matmaster.context.ports import WorkspaceJobs, WorkspaceJobsQuery

    table = MagicMock()
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace=None,
        exporter=_exporter(),
        job_context_mode="workspace_observation",
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))
    assert result == WorkspaceJobs.empty()
    table.query_workspace_active.assert_not_called()


def test_ports_do_not_construct_table_until_identity_allows_use() -> None:
    table_factory = MagicMock(side_effect=AssertionError("should stay lazy"))
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="",
        workspace="/share/project",
        exporter=_exporter(),
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
    )


@pytest.mark.asyncio
async def test_delivery_under_row_limit_returns_full_pending_no_active_query() -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    table = MagicMock()
    snap = _snapshot(
        [
            {"id": 1, "job_id": "t1", "job_name": "ok", "status": "finished"},
            {"id": 2, "job_id": "f1", "job_name": "bad", "status": "failed"},
        ]
    )
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
        table=table,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.mode == "session_workspace_delivery"
    assert result.unhandled_terminal_jobs == snap.rows
    assert result.active_jobs == ()
    assert result.export is None
    assert result.summary.unhandled_terminal == 2
    table.query_session_active.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_over_row_limit_exports_pending_only(monkeypatch) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    table = MagicMock()
    rows = tuple(
        {"id": i, "job_id": f"j{i}", "job_name": "n", "status": "finished"}
        for i in range(3)
    )
    rows += ({"id": 99, "job_id": "f1", "job_name": "bad", "status": "failed"},)
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=MagicMock()),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=_snapshot(rows),
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is not None
    assert result.export.reason == "row_limit"
    assert result.export.row_count == len(rows)
    assert result.unhandled_terminal_jobs == ()
    assert result.preview_rows
    assert any(s["job_id"] == "f1" for s in result.preview_rows)


@pytest.mark.asyncio
async def test_delivery_export_failure_writes_snapshot_export_failure(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "1")
    table = MagicMock()
    rows = tuple(
        {"id": i, "job_id": f"j{i}", "job_name": "n", "status": "failed"}
        for i in range(3)
    )
    snap = _snapshot(rows)
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
        table=table,
    )
    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export_error is not None
    assert result.export_error.reason == "session_missing"
    assert snap.export_failure["reason"] == "session_missing"
    assert snap.export_failure["target_path"]


@pytest.mark.asyncio
async def test_delivery_inline_threshold_is_row_only_even_for_long_names(
    monkeypatch,
) -> None:
    from matmaster.context.ports import WorkspaceJobsQuery

    monkeypatch.setenv("BOHRIUM_WORKSPACE_JOBS_PROMPT_PREVIEW_LIMIT", "2")
    snap = _snapshot(
        [{"id": 1, "job_id": "t1", "job_name": "n" * 20000, "status": "finished"}]
    )
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(session=None),
        job_context_mode="session_workspace_delivery",
        delivery_snapshot=snap,
    )

    result = await jobs_port.load_workspace_jobs(WorkspaceJobsQuery(session_id="s"))

    assert result.export is None
    assert result.unhandled_terminal_jobs == snap.rows


def test_record_poll_terminal_feeds_observed_set() -> None:
    table = MagicMock()
    snap = _snapshot([])
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s",
        invocation_id="inv",
        user_id="u",
        org_id="o",
        workspace="/share/project",
        exporter=_exporter(),
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
        exporter=_exporter(),
        table=table,
    )
    ledger.record_poll(job_id="J", sandbox=False, status_code=2)
    table.apply_poll.assert_called_once()
