from __future__ import annotations

import pymysql
import pytest

_INSERT = """
    INSERT INTO bohrium_jobs
        (session_id, invocation_id, user_id, org_id, job_id, project_id, sandbox,
         input_dir, status, next_poll_at, terminal_at, result_dir, handled_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def _insert(conn, **kw):
    with conn.cursor() as cur:
        cur.execute(
            _INSERT,
            (
                kw.get("session_id", "s"),
                kw.get("invocation_id", "inv"),
                kw.get("user_id", "u"),
                kw.get("org_id", "o"),
                kw.get("job_id", "j"),
                kw.get("project_id", 1),
                kw.get("sandbox", 1),
                kw.get("input_dir", "in"),
                kw["status"],
                kw.get("next_poll_at"),
                kw.get("terminal_at"),
                kw.get("result_dir"),
                kw.get("handled_at"),
            ),
        )
    conn.commit()


def test_chk_status_rejects_case_variant(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(
            db_conn,
            status="Finished",
            next_poll_at=None,
            terminal_at="2026-06-01 00:00:00",
        )
    db_conn.rollback()


def test_chk_active_poll_requires_next_poll(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(db_conn, status="running", next_poll_at=None, terminal_at=None)
    db_conn.rollback()


def test_chk_terminal_requires_terminal_at(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(db_conn, status="finished", next_poll_at=None, terminal_at=None)
    db_conn.rollback()


def test_chk_handled_requires_terminal(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(
            db_conn,
            status="running",
            next_poll_at="2026-06-01 00:00:00",
            terminal_at=None,
            handled_at="2026-06-01 00:00:00",
        )
    db_conn.rollback()


def test_chk_sandbox_rejects_out_of_range(db_conn) -> None:
    with pytest.raises(pymysql.err.OperationalError):
        _insert(
            db_conn,
            status="running",
            next_poll_at="2026-06-01 00:00:00",
            terminal_at=None,
            sandbox=2,
        )
    db_conn.rollback()


def test_timestamp_utc_anchored_across_connection_timezones(
    bohrium_jobs_db_config,
) -> None:
    cfg = dict(bohrium_jobs_db_config)
    w = pymysql.connect(**cfg)
    try:
        with w.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
            cur.execute("""
                INSERT INTO bohrium_jobs
                    (session_id, invocation_id, user_id, org_id, job_id,
                     project_id, sandbox, input_dir, status, next_poll_at,
                     terminal_at, result_dir)
                VALUES
                    ('s', 'inv', 'u', 'o', 'tz1', 1,
                     1, 'in', 'running', NOW() - INTERVAL 5 SECOND, NULL, NULL)
                """)
        w.commit()
    finally:
        w.close()

    for tz in ("+00:00", "+08:00", "-05:00"):
        c = pymysql.connect(**cfg)
        try:
            with c.cursor() as cur:
                cur.execute(f"SET time_zone = '{tz}'")
                cur.execute(
                    "SELECT (next_poll_at <= NOW()) AS due FROM bohrium_jobs "
                    "WHERE job_id='tz1'"
                )
                assert cur.fetchone()["due"] == 1
        finally:
            c.close()
