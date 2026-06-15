"""DeliverySnapshot 的构造与 confirm 范围：snapshot 持全量行，
confirm ack snapshot rows 与 run 内前台观察集的并集。"""

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


def test_snapshot_holds_full_rows():
    rows = [
        _row(11, "f1", status="failed"),
        _row(12, "t1"),
        _row(13, "t2", inv=None),
    ]
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = rows

    snap = bohrium_delivery_ack.snapshot(
        "sess-1",
        workspace="/share/project",
        sessions_service=_sessions(),
        jobs_table=table,
    )

    assert snap.user_id == "u1" and snap.org_id == "o1"
    assert snap.session_id == "sess-1"
    assert snap.workspace == "/share/project"
    assert snap.rows == tuple(rows)  # DAO 失败优先序原样保持
    assert snap.rows[0]["result_dir"] == "/share/project/out/f1"  # 取结果字段在场
    kw = table.list_pending_terminal_snapshot.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "sess-1",
        "workspace": "/share/project",
    }


def test_snapshot_empty_rows_returns_object_not_none():
    # 身份可解析即返回对象：空 rows 是合法交付边界，观察集空集起步。
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = []
    snap = bohrium_delivery_ack.snapshot(
        "sess-1",
        workspace="/share/project",
        sessions_service=_sessions(),
        jobs_table=table,
    )
    assert snap is not None
    assert snap.rows == ()
    assert snap.observed_terminal == set()


def test_snapshot_returns_none_without_org_binding():
    svc = MagicMock()
    svc.get_session.return_value = {"user_id": "u1", "org_id": None}
    table = MagicMock()
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1",
            workspace="/share/project",
            sessions_service=svc,
            jobs_table=table,
        )
        is None
    )
    table.list_pending_terminal_snapshot.assert_not_called()


def test_snapshot_rows_query_failure_degrades_to_empty_rows():
    # rows 查询失败但身份正常：本轮不渲染存量 pending，观察集仍可工作。
    table = MagicMock()
    table.list_pending_terminal_snapshot.side_effect = RuntimeError("db down")
    snap = bohrium_delivery_ack.snapshot(
        "sess-1",
        workspace="/share/project",
        sessions_service=_sessions(),
        jobs_table=table,
    )
    assert snap is not None and snap.rows == ()


def test_confirm_acks_exactly_snapshot_row_ids():
    table = MagicMock()
    table.list_pending_terminal_snapshot.return_value = [_row(11, "a"), _row(12, "b")]
    snap = bohrium_delivery_ack.snapshot(
        "sess-1",
        workspace="/share/project",
        sessions_service=_sessions(),
        jobs_table=table,
    )
    table.mark_handled_by_ids.return_value = 2

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 2
    kw = table.mark_handled_by_ids.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "sess-1",
        "workspace": "/share/project",
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
        workspace="/share/project",
        rows=(_row(1, "a"),),
    )
    with pytest.raises(RuntimeError):
        bohrium_delivery_ack.confirm(snap, jobs_table=table)


def test_snapshot_returns_none_when_session_missing():
    svc = MagicMock()
    svc.get_session.return_value = None
    table = MagicMock()
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1",
            workspace="/share/project",
            sessions_service=svc,
            jobs_table=table,
        )
        is None
    )
    table.list_pending_terminal_snapshot.assert_not_called()


def test_snapshot_identity_lookup_failure_returns_none():
    svc = MagicMock()
    svc.get_session.side_effect = RuntimeError("db down")
    assert (
        bohrium_delivery_ack.snapshot(
            "s",
            workspace="/share/project",
            sessions_service=svc,
            jobs_table=MagicMock(),
        )
        is None
    )


def test_confirm_acks_union_of_rows_and_observed():
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 2
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "a"), _row(12, "b")),
    )
    snap.observed_terminal.add((True, "J"))

    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 3
    assert table.mark_handled_by_ids.call_args.kwargs == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "s",
        "workspace": "/share/project",
        "row_ids": (11, 12),
    }
    kw = table.mark_handled_by_job_keys.call_args.kwargs
    assert kw == {
        "user_id": "u1",
        "org_id": "o1",
        "session_id": "s",
        "workspace": "/share/project",
        "job_keys": ((True, "J"),),
    }


def test_confirm_skips_dao_calls_for_empty_sets():
    table = MagicMock()
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(),
    )
    assert bohrium_delivery_ack.confirm(snap, jobs_table=table) == 0
    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_not_called()


def test_snapshot_returns_none_without_workspace():
    table = MagicMock()
    sessions = _sessions()
    assert (
        bohrium_delivery_ack.snapshot(
            "sess-1", workspace=None, sessions_service=sessions, jobs_table=table
        )
        is None
    )
    sessions.get_session.assert_not_called()
    table.list_pending_terminal_snapshot.assert_not_called()


def test_confirm_skips_rows_when_export_failed_but_acks_observed():
    table = MagicMock()
    table.mark_handled_by_job_keys.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "f1", status="failed"),),
        export_failure={
            "reason": "write_failed",
            "rows": 1,
            "target_path": "/share/project/x.csv",
        },
    )
    snap.observed_terminal.add((True, "J"))

    affected = bohrium_delivery_ack.confirm(snap, jobs_table=table)

    table.mark_handled_by_ids.assert_not_called()
    table.mark_handled_by_job_keys.assert_called_once()
    assert affected == 1


def test_confirm_acks_rows_when_export_failure_empty():
    table = MagicMock()
    table.mark_handled_by_ids.return_value = 1
    snap = bohrium_delivery_ack.DeliverySnapshot(
        user_id="u1",
        org_id="o1",
        session_id="s",
        workspace="/share/project",
        rows=(_row(11, "t1"),),
    )

    affected = bohrium_delivery_ack.confirm(snap, jobs_table=table)

    table.mark_handled_by_ids.assert_called_once()
    assert affected == 1
