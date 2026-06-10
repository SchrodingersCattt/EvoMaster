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
        workspace="/share/project",
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
    assert row["workspace"] == "/share/project"


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
                    "workspace": "/share/project",
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


def test_claim_due_batch_returns_workspace(jobs_table) -> None:
    jobs_table.insert_submitted(
        **_submit_kwargs(job_id="301", workspace="/share/project/a")
    )

    rows = jobs_table.claim_due_batch(limit=10, claim_timeout_seconds=120)

    assert rows[0]["job_id"] == "301"
    assert rows[0]["workspace"] == "/share/project/a"


def test_insert_submitted_rejects_workspace_outside_share(jobs_table) -> None:
    with pytest.raises(ValueError, match="bohrium_jobs.workspace"):
        jobs_table.insert_submitted(
            **_submit_kwargs(job_id="302", workspace="/tmp/project")
        )


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


class _StubPoller:
    def __init__(self, summary=None, exc=None):
        self._summary = summary or {"claimed": 0, "polled": 0, "errors": 0}
        self._exc = exc
        self.calls: list[dict] = []

    def run_once(self, *, limit, claim_timeout_seconds):
        self.calls.append(
            {"limit": limit, "claim_timeout_seconds": claim_timeout_seconds}
        )
        if self._exc is not None:
            raise self._exc
        return self._summary


def test_monitor_tick_passes_through_summary() -> None:
    from src.services.bohrium_poller import BohriumMonitor

    stub = _StubPoller(summary={"claimed": 3, "polled": 2, "errors": 1})
    monitor = BohriumMonitor(poller=stub, limit=7, claim_timeout_seconds=99)

    summary = monitor.tick()

    assert summary == {"claimed": 3, "polled": 2, "errors": 1}
    assert stub.calls == [{"limit": 7, "claim_timeout_seconds": 99}]


def test_monitor_tick_swallows_injected_poller_exception() -> None:
    from src.services.bohrium_poller import BohriumMonitor

    stub = _StubPoller(exc=RuntimeError("db down"))
    monitor = BohriumMonitor(poller=stub)

    summary = monitor.tick()

    assert summary == {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}


def test_monitor_default_construct_is_db_free_and_lazy(monkeypatch) -> None:
    """Default construction does not touch DB; tick lazily creates poller."""
    import src.services.bohrium_poller as mod

    class _BoomPoller:
        def __init__(self):
            raise RuntimeError("no DB at construct time")

    monkeypatch.setattr(mod, "BohriumJobPoller", _BoomPoller)

    monitor = mod.BohriumMonitor()
    summary = monitor.tick()

    assert summary == {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}


def test_monitor_default_construct_reads_env_into_run_once(monkeypatch) -> None:
    import src.services.bohrium_poller as mod

    monkeypatch.setenv("BOHRIUM_MONITOR_LIMIT", "8")
    monkeypatch.setenv("BOHRIUM_MONITOR_CLAIM_TIMEOUT", "33")
    captured: dict[str, int] = {}

    class _StubDefaultPoller:
        def __init__(self):
            pass

        def run_once(self, *, limit, claim_timeout_seconds):
            captured["limit"] = limit
            captured["claim_timeout_seconds"] = claim_timeout_seconds
            return {"claimed": 0, "polled": 0, "errors": 0}

    monkeypatch.setattr(mod, "BohriumJobPoller", _StubDefaultPoller)

    mod.BohriumMonitor().tick()

    assert captured == {"limit": 8, "claim_timeout_seconds": 33}


def test_env_int_missing_and_invalid_fall_back(monkeypatch) -> None:
    from src.utils.constant import env_int

    monkeypatch.delenv("BOHRIUM_X", raising=False)
    assert env_int("BOHRIUM_X", 5) == 5
    monkeypatch.setenv("BOHRIUM_X", "not-an-int")
    assert env_int("BOHRIUM_X", 5) == 5
    monkeypatch.setenv("BOHRIUM_X", "12")
    assert env_int("BOHRIUM_X", 5) == 12


def _make_capture_table(captured: list[dict]):
    class _Table:
        def claim_due_batch(self, *, limit: int, claim_timeout_seconds: int):
            return [
                {
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "org_id": "org-1",
                    "project_id": 42,
                    "job_id": "101",
                    "sandbox": False,
                    "workspace": "/share/project",
                    "status": "submitted",
                    "poll_count": 0,
                }
            ]

        def apply_poll(self, **kw):
            raise AssertionError(f"unexpected poll success: {kw}")

        def mark_poll_error(self, **kw):
            captured.append(kw)

    return _Table()


def test_poller_passes_lost_after_to_mark_error() -> None:
    captured: list[dict] = []
    poller = BohriumJobPoller(
        table=_make_capture_table(captured),
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
        lost_after_seconds=1234,
    )
    poller.run_once()
    assert captured[0]["lost_after_seconds"] == 1234


def test_poller_lost_after_defaults_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_POLL_LOST_AFTER_SECONDS", "555")
    captured: list[dict] = []
    poller = BohriumJobPoller(
        table=_make_capture_table(captured),
        get_access_key=lambda uid, oid: None,
        get_job_detail=lambda ctx, job_id: {"status": 1},
        base_url="https://x",
    )
    poller.run_once()
    assert captured[0]["lost_after_seconds"] == 555
