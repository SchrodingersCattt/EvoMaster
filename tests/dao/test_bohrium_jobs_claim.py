from __future__ import annotations

import pymysql


def _seed_active(jobs_table, n: int) -> None:
    for i in range(n):
        jobs_table.insert_submitted(
            session_id="sess-1",
            invocation_id="inv-1",
            spawn_id=None,
            user_id="user-1",
            org_id="org-1",
            job_id=f"j{i}",
            job_name=None,
            project_id=42,
            sandbox=True,
            input_dir="data/in",
        )


def test_claim_due_batch_skips_terminal_jobs(jobs_table) -> None:
    _seed_active(jobs_table, 2)
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="j1",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    ids = {c["job_id"] for c in claimed}
    assert ids == {"j0"}


def test_claim_due_batch_disjoint_under_concurrency(
    bohrium_jobs_db_config, jobs_table
) -> None:
    _seed_active(jobs_table, 4)
    from src.dao.bohrium_jobs_table import BohriumJobsTable

    t_a = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    t_b = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    conn_a = pymysql.connect(**bohrium_jobs_db_config)
    conn_b = pymysql.connect(**bohrium_jobs_db_config)
    try:
        a = t_a._select_due_for_update(conn_a, limit=2)
        b = t_b._select_due_for_update(conn_b, limit=2)
        ids_a = {r["job_id"] for r in a}
        ids_b = {r["job_id"] for r in b}
        assert ids_a.isdisjoint(ids_b)
        assert len(ids_a) == 2 and len(ids_b) == 2
    finally:
        conn_a.rollback()
        conn_a.close()
        conn_b.rollback()
        conn_b.close()


def test_claim_places_future_next_poll(jobs_table) -> None:
    _seed_active(jobs_table, 1)
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert len(claimed) == 1
    again = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert again == []


def test_claim_returns_poll_count_snapshot(jobs_table) -> None:
    _seed_active(jobs_table, 1)
    claimed = jobs_table.claim_due_batch(limit=50, claim_timeout_seconds=120)
    assert claimed and "poll_count" in claimed[0]
    assert claimed[0]["poll_count"] == 0
