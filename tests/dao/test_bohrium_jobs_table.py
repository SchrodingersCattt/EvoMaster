from __future__ import annotations

import pytest


def _submit_kwargs(**over):
    base = dict(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="user-1",
        org_id="org-1",
        job_id="12345",
        job_name="matmaster-job",
        project_id=42,
        sandbox=True,
        input_dir="data/in",
        workspace="/share/project",
    )
    base.update(over)
    return base


def test_insert_submitted_sets_active_invariants(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "submitted"
    assert row["next_poll_at"] is not None
    assert row["terminal_at"] is None
    assert row["next_poll_at"] == row["submitted_at"]
    assert row["sandbox"] == 1
    assert row["project_id"] == 42
    assert row["input_dir"] == "data/in"
    assert row["workspace"] == "/share/project"
    assert row["invocation_id"] == "inv-1"
    assert row["spawn_id"] is None


def test_insert_submitted_rejects_sentinel_project_id(jobs_table) -> None:
    with pytest.raises(ValueError):
        jobs_table.insert_submitted(**_submit_kwargs(project_id=-1))
    with pytest.raises(ValueError):
        jobs_table.insert_submitted(**_submit_kwargs(project_id=0))


def test_insert_submitted_upsert_is_idempotent(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.insert_submitted(**_submit_kwargs(job_name="renamed"))
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 1


def test_unique_key_spans_owner_sandbox_jobid(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(sandbox=True, job_id="999"))
    jobs_table.insert_submitted(**_submit_kwargs(sandbox=False, job_id="999"))
    jobs_table.insert_submitted(**_submit_kwargs(org_id="org-2", job_id="999"))
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 3


def test_binary_collation_is_case_sensitive(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="AbC123"))
    jobs_table.insert_submitted(**_submit_kwargs(job_id="abc123"))
    rows = jobs_table.list_all_for_test()
    assert len(rows) == 2
    assert (
        jobs_table.get_by_owner_job(
            user_id="user-1", org_id="org-1", sandbox=True, job_id="abc123"
        )
        is not None
    )


def test_apply_poll_running_advances_next_poll(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="12345",
        status="running",
        is_terminal=False,
        backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "running"
    assert row["poll_count"] == 1
    assert row["last_polled_at"] is not None
    assert row["next_poll_at"] is not None
    assert row["terminal_at"] is None


def test_apply_poll_terminal_sets_terminal_at_and_stops_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="12345",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None


def test_apply_poll_does_not_revert_terminal_to_active(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="12345",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="12345",
        status="running",
        is_terminal=False,
        backoff_seconds=30,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None
    assert row["poll_count"] == 2


def test_apply_kill_sets_terminating_keeps_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs())
    jobs_table.apply_kill(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="12345"
    )
    assert row["status"] == "terminating"
    assert row["next_poll_at"] is not None
    assert row["terminal_at"] is None


def test_query_session_active_returns_active_only_sorted(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="a1"))
    jobs_table.insert_submitted(**_submit_kwargs(job_id="a2"))
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="a2",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    active = jobs_table.query_session_active(
        user_id="user-1", org_id="org-1", session_id="sess-1"
    )
    ids = [j["job_id"] for j in active]
    assert ids == ["a1"]
    j = active[0]
    assert set(j.keys()) == {
        "job_id",
        "job_name",
        "status",
        "sandbox",
        "project_id",
        "input_dir",
        "workspace",
        "submitted_at",
        "last_polled_at",
        "result_dir",
    }
    assert j["sandbox"] is True


def test_query_session_pending_terminal(jobs_table) -> None:
    for jid in ["t1", "t2", "t3"]:
        jobs_table.insert_submitted(**_submit_kwargs(job_id=jid))
        jobs_table.apply_poll(
            user_id="user-1",
            org_id="org-1",
            sandbox=True,
            job_id=jid,
            status="finished",
            is_terminal=True,
            backoff_seconds=30,
        )
    pending = jobs_table.query_session_pending_terminal(
        user_id="user-1", org_id="org-1", session_id="sess-1", limit=5
    )
    assert len(pending) == 3
    assert all(j["status"] in {"finished", "failed", "stopped"} for j in pending)
    with jobs_table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bohrium_jobs SET handled_at = NOW() WHERE job_id = 't1'"
            )
        conn.commit()
    pending2 = jobs_table.query_session_pending_terminal(
        user_id="user-1", org_id="org-1", session_id="sess-1", limit=5
    )
    assert {j["job_id"] for j in pending2} == {"t2", "t3"}


def test_mark_poll_error_marks_active_unknown(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e1"))
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e1",
        backoff_seconds=45,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e1"
    )
    assert row["status"] == "unknown"
    assert row["next_poll_at"] is not None
    assert row["terminal_at"] is None


def test_mark_poll_error_does_not_touch_terminal(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="e2"))
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e2",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    jobs_table.mark_poll_error(
        user_id="user-1",
        org_id="org-1",
        sandbox=True,
        job_id="e2",
        backoff_seconds=45,
    )
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=True, job_id="e2"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None
