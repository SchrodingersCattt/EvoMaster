"""DeliverySnapshot 的构造与 confirm 范围：snapshot 持全量 row/job ids 与行，
confirm 只 ack snapshot.row_ids（交付边界 = 查询执行瞬间）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services import bohrium_delivery_ack


def _row(rid: int, job_id: str, status: str = "finished", inv: str | None = "inv-1"):
    # 形状 = BohriumJobsTable._to_snapshot_job 的输出（_AGENT_COLUMNS + 附加三字段）
    return {
        "job_id": job_id,
        "job_name": f"name-{job_id}",
        "status": status,
        "sandbox": False,
        "project_id": 42,
        "input_dir": "data/in",
        "workspace": "/share/project",
        "submitted_at": "2026-06-10 00:00:00",
        "last_polled_at": "2026-06-10 00:05:00",
        "result_dir": f"/share/project/out/{job_id}",
        "id": rid,
        "invocation_id": inv,
        "terminal_at": "2026-06-10 00:05:00",
    }


def _sessions(user="u1", org="o1"):
    svc = MagicMock()
    svc.get_session.return_value = {"user_id": user, "org_id": org}
    return svc


def test_snapshot_holds_full_ids_rows_and_counts():
    rows = [
        _row(11, "f1", status="failed"),
        _row(12, "t1"),
        _row(13, "t2", inv=None),
    ]
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = rows

    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )

    assert snap.user_id == "u1" and snap.org_id == "o1"
    assert snap.session_id == "sess-1"
    assert snap.row_ids == (11, 12, 13)  # DAO 失败优先序原样保持
    assert snap.job_ids == ("f1", "t1", "t2")
    assert snap.rows == tuple(rows)
    assert snap.rows[0]["result_dir"] == "/share/project/out/f1"  # 取结果字段在场
    assert snap.status_counts == {"failed": 1, "finished": 2}
    assert snap.invocation_counts == {"inv-1": 2, "": 1}
    kw = table.list_pending_terminal_snapshot.call_args.kwargs
    assert kw == {"user_id": "u1", "org_id": "o1", "session_id": "sess-1"}


def test_snapshot_reads_detail_limit_from_env(monkeypatch):
    monkeypatch.setenv("BOHRIUM_DELIVERY_DETAIL_LIMIT", "7")
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(1, "a")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    assert snap.detail_limit == 7


def test_snapshot_returns_none_when_no_pending_rows():
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = []
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", sessions_service=_sessions(), jobs_table=table
        )
        is None
    )


def test_snapshot_returns_none_without_org_binding():
    svc = MagicMock()
    svc.get_session.return_value = {"user_id": "u1", "org_id": None}
    table = MagicMock()
    assert (
        bohrium_delivery_ack.snapshot("sess-1", sessions_service=svc, jobs_table=table)
        is None
    )
    table.list_pending_terminal_snapshot.assert_not_called()


def test_snapshot_returns_none_on_query_failure_without_raising():
    table = MagicMock()
    table.list_pending_terminal_snapshot.side_effect = RuntimeError("db down")
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", sessions_service=_sessions(), jobs_table=table
        )
        is None
    )


def test_confirm_acks_exactly_snapshot_row_ids():
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(11, "a"), _row(12, "b")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    table.mark_handled_by_ids.return_value = 2

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 2
    kw = table.mark_handled_by_ids.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "sess-1",
        "row_ids": (11, 12),
    }


def test_confirm_propagates_failure_to_caller():
    # worker 层负责吞掉并继续 release；confirm 本身不掩盖失败
    table = MagicMock()
    table.mark_handled_by_ids.side_effect = RuntimeError("db down")
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        row_ids=(1,),
        job_ids=("a",),
        rows=(_row(1, "a"),),
        status_counts={"finished": 1},
        invocation_counts={"inv-1": 1},
        detail_limit=20,
    )
    with pytest.raises(RuntimeError):
        bohrium_delivery_ack.confirm(snap, jobs_table=table)


def test_snapshot_detail_limit_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("BOHRIUM_DELIVERY_DETAIL_LIMIT", raising=False)
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(1, "a")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1", sessions_service=_sessions(), jobs_table=table
    )
    assert snap.detail_limit == 20


def test_snapshot_returns_none_when_session_missing():
    svc = MagicMock()
    svc.get_session.return_value = None
    table = MagicMock()
    assert (
        bohrium_delivery_ack.snapshot("sess-1", sessions_service=svc, jobs_table=table)
        is None
    )
    table.list_pending_terminal_snapshot.assert_not_called()
