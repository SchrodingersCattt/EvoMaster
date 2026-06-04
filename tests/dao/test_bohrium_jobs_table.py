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
