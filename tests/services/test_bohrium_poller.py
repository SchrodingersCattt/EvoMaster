from __future__ import annotations

import pymysql
import pytest

from src.services.bohrium_poller import BohriumJobPoller, compute_poll_backoff
from tests.dao.conftest import _SQL_FILE, _test_db_config


@pytest.fixture(scope="session")
def _poller_db_config():
    cfg = _test_db_config()
    try:
        conn = pymysql.connect(**cfg)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bohrium_jobs poller tests require MySQL from .env.test: {exc}")
    ddl = _SQL_FILE.read_text(encoding="utf-8").rstrip().rstrip(";")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            version = str(cur.fetchone()["v"])
            major_minor_patch = tuple(
                int(p) for p in version.split("-")[0].split(".")[:3]
            )
            if major_minor_patch < (8, 0, 16):
                pytest.skip(f"bohrium_jobs needs MySQL >= 8.0.16, got {version}")
            cur.execute("DROP TABLE IF EXISTS `bohrium_jobs`")
            cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    return cfg


@pytest.fixture()
def jobs_table(_poller_db_config):
    from src.dao.bohrium_jobs_table import BohriumJobsTable

    table = BohriumJobsTable(db_config=_poller_db_config)
    with table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE `bohrium_jobs`")
        conn.commit()
    return table


def _submit_kwargs(**over):
    base = dict(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="user-1",
        org_id="org-1",
        job_id="101",
        job_name=None,
        project_id=42,
        sandbox=False,
        input_dir="data/in",
    )
    base.update(over)
    return base


def test_compute_poll_backoff_grows_and_caps() -> None:
    assert compute_poll_backoff(0) == 30
    assert compute_poll_backoff(1) == 60
    assert compute_poll_backoff(2) == 120
    assert compute_poll_backoff(99) == 600


def test_poller_polls_due_job_and_writes_running(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="101"))
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://openapi.test.dp.tech",
    )
    summary = poller.run_once()
    assert summary["claimed"] == 1 and summary["polled"] == 1
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="101"
    )
    assert row["status"] == "running"
    assert row["poll_count"] == 1
    assert row["next_poll_at"] is not None


def test_poller_first_poll_uses_initial_backoff() -> None:
    class _Table:
        def __init__(self) -> None:
            self.backoff_seconds: int | None = None

        def claim_due_batch(self, *, limit: int, claim_timeout_seconds: int):
            return [
                {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "org_id": "org-1",
                    "project_id": 42,
                    "job_id": "101",
                    "sandbox": False,
                    "status": "submitted",
                    "poll_count": 0,
                }
            ]

        def apply_poll(self, **kw):
            self.backoff_seconds = kw["backoff_seconds"]

        def mark_poll_error(self, **kw):
            raise AssertionError(f"unexpected poll error: {kw}")

    table = _Table()
    poller = BohriumJobPoller(
        table=table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
    )

    assert poller.run_once() == {"claimed": 1, "polled": 1, "errors": 0}
    assert table.backoff_seconds == 30


def test_poller_writes_terminal_and_stops_polling(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="102"))
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=lambda ctx, job_id: {"status": 2},
        base_url="https://x",
    )
    poller.run_once()
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="102"
    )
    assert row["status"] == "finished"
    assert row["next_poll_at"] is None
    assert row["terminal_at"] is not None


def test_poller_skips_terminal_jobs(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="103"))
    jobs_table.apply_poll(
        user_id="user-1",
        org_id="org-1",
        sandbox=False,
        job_id="103",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )
    calls = []
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: (calls.append("ak"), "AK")[1],
        get_job_detail=lambda ctx, job_id: (calls.append("detail"), {"status": 2})[1],
        base_url="https://x",
    )
    summary = poller.run_once()
    assert summary["claimed"] == 0
    assert calls == []


def test_poller_caches_access_key_within_round(jobs_table) -> None:
    for jid in ["201", "202", "203"]:
        jobs_table.insert_submitted(**_submit_kwargs(job_id=jid))
    ak_calls = []

    def _get_ak(uid, oid):
        ak_calls.append((uid, oid))
        return "AK"

    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=_get_ak,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
    )
    poller.run_once()
    assert ak_calls == [("user-1", "org-1")]


def test_poller_marks_unknown_on_detail_exception(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="204"))

    def _boom(ctx, job_id):
        raise RuntimeError("api 500")

    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: "AK",
        get_job_detail=_boom,
        base_url="https://x",
    )
    summary = poller.run_once()
    assert summary["errors"] == 1
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="204"
    )
    assert row["status"] == "unknown"
    assert row["next_poll_at"] is not None


def test_poller_marks_unknown_when_access_key_missing(jobs_table) -> None:
    jobs_table.insert_submitted(**_submit_kwargs(job_id="205"))
    detail_calls = []
    poller = BohriumJobPoller(
        table=jobs_table,
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: (detail_calls.append(1), {"status": 1})[1],
        base_url="https://x",
    )
    poller.run_once()
    row = jobs_table.get_by_owner_job(
        user_id="user-1", org_id="org-1", sandbox=False, job_id="205"
    )
    assert row["status"] == "unknown"
    assert row["next_poll_at"] is not None
    assert detail_calls == []
