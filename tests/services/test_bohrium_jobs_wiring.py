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


def test_record_submit_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="",
        org_id="o1",
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


def test_record_poll_fails_when_identity_missing() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="sess-1",
        invocation_id="inv-1",
        user_id="",
        org_id="o1",
        table=table,
    )
    with pytest.raises(ValueError):
        ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    table.apply_poll.assert_not_called()


def test_record_poll_normalizes_status_code() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s", invocation_id="inv", user_id="u", org_id="o", table=table
    )
    ledger.record_poll(job_id="1", sandbox=False, status_code=2)
    kw = table.apply_poll.call_args.kwargs
    assert kw["status"] == "finished"
    assert kw["is_terminal"] is True


def test_mark_handled_delegates_to_dao() -> None:
    table = MagicMock()
    ledger, _ = build_bohrium_jobs_ports(
        session_id="s", invocation_id="inv", user_id="u", org_id="o", table=table
    )
    ledger.mark_handled(job_id="1", sandbox=True)
    kw = table.mark_handled.call_args.kwargs
    assert kw["user_id"] == "u" and kw["org_id"] == "o"
    assert kw["job_id"] == "1" and kw["sandbox"] is True


@pytest.mark.asyncio
async def test_session_jobs_port_loads_active_and_pending() -> None:
    table = MagicMock()
    table.query_session_active.return_value = [{"job_id": "a"}]
    table.query_session_pending_terminal.return_value = [{"job_id": "t"}]
    _, jobs_port = build_bohrium_jobs_ports(
        session_id="s", invocation_id="inv", user_id="u", org_id="o", table=table
    )
    from matmaster.context.ports import SessionJobsQuery

    result = await jobs_port.load_session_jobs(SessionJobsQuery(session_id="s"))
    assert result.active_jobs == ({"job_id": "a"},)
    assert result.pending_terminal_jobs == ({"job_id": "t"},)
    assert table.query_session_active.call_args.kwargs["user_id"] == "u"
    assert table.query_session_active.call_args.kwargs["org_id"] == "o"


def test_session_identity_resolution_helper_uses_session_snapshot() -> None:
    from src.services import agent_run_service as ars

    captured = {}

    class _FakeSessions:
        def get_session(self, sid):
            captured["sid"] = sid
            return {"user_id": "user-from-db", "org_id": "org-from-db"}

    user, org = ars._resolve_session_identity("sess-1", sessions_table=_FakeSessions())
    assert user == "user-from-db"
    assert org == "org-from-db"
    assert captured["sid"] == "sess-1"


def test_session_identity_resolution_prefers_explicit_run_user_id() -> None:
    from src.services import agent_run_service as ars

    class _FakeSessions:
        def get_session(self, sid):
            return {"user_id": "user-from-db", "org_id": "org-from-db"}

    user, org = ars._resolve_session_identity(
        "sess-1", user_id="user-from-run", sessions_table=_FakeSessions()
    )
    assert user == "user-from-run"
    assert org == "org-from-db"
