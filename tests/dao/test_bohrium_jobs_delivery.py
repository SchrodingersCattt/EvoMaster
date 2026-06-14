"""scan_delivery_units / list_pending_terminal_snapshot / mark_handled_by_ids /
get_first_pending_failed 的真库测试（无 .env.test 则整组 SKIP）。"""

from __future__ import annotations

import pymysql
import pytest


@pytest.fixture()
def sessions_shadow(bohrium_jobs_db_config):
    """scan_delivery_units 的 EXISTS 谓词只点查 user_id/org_id/session_id，
    建最小影子表足够（测试库名由 conftest 强制 *_test，DROP 安全）。"""
    conn = pymysql.connect(**bohrium_jobs_db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS `evo_chat_sessions`")
            cur.execute("""
                CREATE TABLE `evo_chat_sessions` (
                    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                    `user_id` VARCHAR(255) NULL,
                    `org_id` VARCHAR(255) NULL,
                    `session_id` VARCHAR(255) NOT NULL UNIQUE
                )
                """)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _register_session(conn, *, session="sess-1", user="u1", org="o1"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evo_chat_sessions (user_id, org_id, session_id) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE user_id=VALUES(user_id), org_id=VALUES(org_id)",
            (user, org, session),
        )
    conn.commit()


def _seed_job(
    jobs_table,
    *,
    session="sess-1",
    user="u1",
    org="o1",
    inv="inv-1",
    job_id="101",
    sandbox=False,
    status=None,
):
    """插入一行；status 传 'finished'/'failed'/'stopped' 时推进到终态。"""
    jobs_table.insert_submitted(
        session_id=session,
        invocation_id=inv,
        spawn_id=None,
        user_id=user,
        org_id=org,
        job_id=job_id,
        job_name=f"name-{job_id}",
        project_id=42,
        sandbox=sandbox,
        input_dir="data/in",
        workspace="/share/project",
    )
    if status is not None:
        jobs_table.apply_poll(
            user_id=user,
            org_id=org,
            sandbox=sandbox,
            job_id=job_id,
            status=status,
            is_terminal=True,
            backoff_seconds=30,
        )


def _shift_terminal_at(conn, *, job_id, seconds_ago):
    """直接改 terminal_at 制造确定的时间序（apply_poll 走 NOW() 同秒并列）。"""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bohrium_jobs SET terminal_at = NOW() - INTERVAL %s SECOND "
            "WHERE job_id = %s",
            (int(seconds_ago), job_id),
        )
    conn.commit()


def test_scan_aggregates_per_invocation(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    # inv-1：1 失败终态未交付 + 1 活跃
    _seed_job(jobs_table, inv="inv-1", job_id="101", status="failed")
    _seed_job(jobs_table, inv="inv-1", job_id="102")
    # inv-2：1 成功终态未交付
    _seed_job(jobs_table, inv="inv-2", job_id="201", status="finished")

    units = jobs_table.scan_delivery_units(limit=10)

    assert [u["invocation_key"] for u in units] == ["inv-1", "inv-2"] or [
        u["invocation_key"] for u in units
    ] == ["inv-2", "inv-1"]
    by_key = {u["invocation_key"]: u for u in units}
    u1 = by_key["inv-1"]
    assert u1["total"] == 2 and u1["active"] == 1
    assert u1["pending_terminal"] == 1
    assert u1["failed_total"] == 1 and u1["failed_handled"] == 0
    assert u1["succeeded"] == 0
    assert u1["workspace"] == "/share/project"
    assert isinstance(u1["max_pending_terminal_id"], int)
    u2 = by_key["inv-2"]
    assert u2["total"] == 1 and u2["active"] == 0
    assert u2["pending_terminal"] == 1 and u2["succeeded"] == 1


def test_scan_excludes_owner_mismatch_rows(jobs_table, sessions_shadow):
    # session 当前 owner 是 (u1, o2)，ledger 行写于 o1 时期 → 必须被 EXISTS 滤掉
    _register_session(sessions_shadow, session="sess-1", user="u1", org="o2")
    _seed_job(jobs_table, org="o1", job_id="101", status="finished")

    assert jobs_table.scan_delivery_units(limit=10) == []


def test_scan_orders_oldest_pending_first_and_limits(jobs_table, sessions_shadow):
    for i, sess in enumerate(("sess-a", "sess-b", "sess-c"), 1):
        _register_session(sessions_shadow, session=sess)
        _seed_job(jobs_table, session=sess, job_id=str(100 + i), status="finished")
    # sess-c 最老，sess-a 最新
    _shift_terminal_at(sessions_shadow, job_id="103", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="102", seconds_ago=200)
    _shift_terminal_at(sessions_shadow, job_id="101", seconds_ago=100)

    units = jobs_table.scan_delivery_units(limit=2)

    assert [u["session_id"] for u in units] == ["sess-c", "sess-b"]


def test_scan_null_invocation_groups_as_empty_key(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv=None, job_id="101", status="finished")
    _seed_job(jobs_table, inv="inv-1", job_id="102", status="finished")

    units = jobs_table.scan_delivery_units(limit=10)

    assert sorted(u["invocation_key"] for u in units) == ["", "inv-1"]


def test_snapshot_returns_full_rows_failed_first_with_fields(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    for i in range(1, 7):  # 6 个成功 → 验证无 limit=5 截断
        _seed_job(jobs_table, job_id=str(100 + i), status="finished")
    _seed_job(jobs_table, job_id="200", status="failed")
    _shift_terminal_at(sessions_shadow, job_id="200", seconds_ago=600)  # 失败行最老

    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
    )

    assert len(rows) == 7
    assert rows[0]["job_id"] == "200" and rows[0]["status"] == "failed"
    first = rows[0]
    # _AGENT_COLUMNS 全集 + id/invocation_id/terminal_at
    for key in (
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
        "id",
        "invocation_id",
        "terminal_at",
    ):
        assert key in first, f"missing field {key}"
    assert isinstance(first["id"], int)
    assert first["terminal_at"] is not None


def test_mark_handled_by_ids_idempotent_and_chunked(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    for i in (1, 2, 3):
        _seed_job(jobs_table, job_id=str(100 + i), status="finished")
    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
    )
    ids = [r["id"] for r in rows]

    # chunk_size=1 强制走分块路径；只标前两个
    affected = jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        row_ids=ids[:2],
        chunk_size=1,
    )
    assert affected == 2
    remaining = jobs_table.list_pending_terminal_snapshot(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
    )
    assert [r["id"] for r in remaining] == [ids[2]]

    # 幂等：重复 ack 是 no-op
    assert (
        jobs_table.mark_handled_by_ids(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            workspace="/share/project",
            row_ids=ids[:2],
        )
        == 0
    )

    # 全部 handled 后该 session 不再出现在扫描里
    jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        row_ids=ids,
    )
    assert jobs_table.scan_delivery_units(limit=10) == []


def test_get_first_pending_failed_returns_earliest_unhandled(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="101", status="failed")
    with sessions_shadow.cursor() as cur:
        cur.execute(
            "UPDATE bohrium_jobs SET handled_at = NOW() WHERE job_id = %s", ("101",)
        )
    sessions_shadow.commit()
    _seed_job(jobs_table, job_id="102", status="stopped")
    _seed_job(jobs_table, job_id="103", status="failed")
    _shift_terminal_at(sessions_shadow, job_id="102", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="103", seconds_ago=100)

    row = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        invocation_key="inv-1",
    )

    assert row == {"job_id": "102", "job_name": "name-102", "status": "stopped"}


def _force_lost(jobs_table, *, job_id):
    """把活跃行拨老后经 mark_poll_error 置 lost（唯一合法写入路径）。"""
    with jobs_table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bohrium_jobs SET submitted_at = NOW() - INTERVAL 7200 SECOND "
                "WHERE job_id = %s",
                (job_id,),
            )
        conn.commit()
    jobs_table.mark_poll_error(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id=job_id,
        backoff_seconds=30,
        lost_after_seconds=3600,
    )


def test_scan_lost_only_unit_has_final_shape(jobs_table, sessions_shadow):
    # 全部作业失联：active 归零、pending_terminal 计入 → decide 判 FINAL。
    # 锚定本方案要修的核心病灶：失联作业不再以 active>0 永久压制 FINAL。
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="401")
    _force_lost(jobs_table, job_id="401")

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["pending_terminal"] == 1
    assert unit["active"] == 0
    assert unit["failed_total"] == 1
    assert unit["failed_handled"] == 0
    assert unit["succeeded"] == 0


def test_scan_lost_with_active_has_first_failure_shape(jobs_table, sessions_shadow):
    # 1 lost + 1 仍在跑：failed_total>0 且 failed_handled==0、active>0
    # → decide 判 FIRST_FAILURE；get_first_pending_failed 取到 lost 行供文案。
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="402")
    _seed_job(jobs_table, inv="inv-1", job_id="403")
    _force_lost(jobs_table, job_id="402")

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["total"] == 2
    assert unit["pending_terminal"] == 1
    assert unit["active"] == 1
    assert unit["failed_total"] == 1
    assert unit["failed_handled"] == 0

    first = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        invocation_key="inv-1",
    )
    assert first is not None
    assert first["status"] == "lost"


def test_mark_handled_by_job_keys_idempotent_and_session_scoped(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="301", status="finished")
    _seed_job(jobs_table, job_id="302")
    _register_session(sessions_shadow, session="sess-2")
    _seed_job(jobs_table, session="sess-2", job_id="303", status="finished")

    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            workspace="/share/project",
            job_keys=[(True, "301")],
        )
        == 0
    )

    affected = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        job_keys=[(False, "301"), (False, "302"), (False, "303")],
    )

    assert affected == 1
    assert (
        jobs_table.list_pending_terminal_snapshot(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            workspace="/share/project",
        )
        == []
    )
    other = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-2", workspace="/share/project"
    )
    assert [r["job_id"] for r in other] == ["303"]

    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            workspace="/share/project",
            job_keys=[(False, "301")],
        )
        == 0
    )
    assert (
        jobs_table.mark_handled_by_job_keys(
            user_id="u1",
            org_id="o1",
            session_id="sess-1",
            workspace="/share/project",
            job_keys=[],
        )
        == 0
    )


def test_snapshot_excludes_other_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="301", status="finished")  # /share/project
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="302",
        job_name="name-302",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="302",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )

    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    assert [r["job_id"] for r in rows] == ["301"]


def test_mark_handled_by_ids_does_not_cross_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="401", status="finished")  # /share/project
    rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    ids = [r["id"] for r in rows]
    # 用错 workspace ack：一行都不命中
    affected = jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        row_ids=ids,
    )
    assert affected == 0
    still = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    assert [r["id"] for r in still] == ids


def test_mark_handled_by_job_keys_does_not_cross_workspace(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="501", status="finished")  # /share/project
    # 正确 workspace + 错 job_key 无效；错 workspace + 对 job_key 也无效
    affected_wrong_ws = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        job_keys=[(False, "501")],
    )
    assert affected_wrong_ws == 0
    affected_ok = jobs_table.mark_handled_by_job_keys(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        job_keys=[(False, "501")],
    )
    assert affected_ok == 1


def test_scan_splits_same_session_by_workspace(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    # 同 session 同 invocation，两个 workspace 各一终态未交付
    _seed_job(jobs_table, inv="inv-1", job_id="601", status="finished")
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="602",
        job_name="name-602",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="602",
        status="finished",
        is_terminal=True,
        backoff_seconds=30,
    )

    units = jobs_table.scan_delivery_units(limit=10)

    workspaces = sorted(u["workspace"] for u in units)
    assert workspaces == ["/share/other", "/share/project"]
    for u in units:
        assert u["pending_terminal"] == 1


def test_get_first_pending_failed_scoped_by_workspace(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, inv="inv-1", job_id="701", status="failed")
    jobs_table.insert_submitted(
        session_id="sess-1",
        invocation_id="inv-1",
        spawn_id=None,
        user_id="u1",
        org_id="o1",
        job_id="702",
        job_name="name-702",
        project_id=42,
        sandbox=False,
        input_dir="data/in",
        workspace="/share/other",
    )
    jobs_table.apply_poll(
        user_id="u1",
        org_id="o1",
        sandbox=False,
        job_id="702",
        status="failed",
        is_terminal=True,
        backoff_seconds=30,
    )

    row_other = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/other",
        invocation_key="inv-1",
    )
    assert row_other is not None and row_other["job_id"] == "702"
    row_project = jobs_table.get_first_pending_failed(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        invocation_key="inv-1",
    )
    assert row_project is not None and row_project["job_id"] == "701"


def test_query_workspace_active_spans_sessions(jobs_table, sessions_shadow):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="601")
    _seed_job(jobs_table, session="sess-B", job_id="602")

    rows = jobs_table.query_workspace_active(
        user_id="u1", org_id="o1", workspace="/share/project"
    )
    assert sorted(r["job_id"] for r in rows) == ["601", "602"]


def test_query_workspace_pending_terminal_spans_sessions_with_limit(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow, session="sess-A")
    _register_session(sessions_shadow, session="sess-B")
    _seed_job(jobs_table, session="sess-A", job_id="701", status="finished")
    _seed_job(jobs_table, session="sess-B", job_id="702", status="finished")

    rows = jobs_table.query_workspace_pending_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert sorted(r["job_id"] for r in rows) == ["701", "702"]

    limited = jobs_table.query_workspace_pending_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=1
    )
    assert len(limited) == 1


def test_query_workspace_recent_terminal_ignores_handled_and_orders_desc(
    jobs_table, sessions_shadow
):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="801", status="finished")
    _seed_job(jobs_table, job_id="802", status="finished")
    _shift_terminal_at(sessions_shadow, job_id="801", seconds_ago=300)
    _shift_terminal_at(sessions_shadow, job_id="802", seconds_ago=100)
    # 把 801 标 handled：recent 仍应包含它（不受 handled_at 影响）
    snap_rows = jobs_table.list_pending_terminal_snapshot(
        user_id="u1", org_id="o1", session_id="sess-1", workspace="/share/project"
    )
    jobs_table.mark_handled_by_ids(
        user_id="u1",
        org_id="o1",
        session_id="sess-1",
        workspace="/share/project",
        row_ids=[r["id"] for r in snap_rows if r["job_id"] == "801"],
    )

    rows = jobs_table.query_workspace_recent_terminal(
        user_id="u1", org_id="o1", workspace="/share/project", limit=10
    )
    assert [r["job_id"] for r in rows] == ["802", "801"]


def test_scan_exposes_unknown_count_and_pending_age(jobs_table, sessions_shadow):
    _register_session(sessions_shadow)
    _seed_job(jobs_table, job_id="501", status="finished")
    _seed_job(jobs_table, job_id="502")
    _seed_job(jobs_table, job_id="503")
    for jid in ("502", "503"):
        jobs_table.mark_poll_error(
            user_id="u1",
            org_id="o1",
            sandbox=False,
            job_id=jid,
            backoff_seconds=30,
            lost_after_seconds=86400,
        )
    _shift_terminal_at(sessions_shadow, job_id="501", seconds_ago=600)

    units = jobs_table.scan_delivery_units(limit=10)

    assert len(units) == 1
    unit = units[0]
    assert unit["unknown_count"] == 2
    assert unit["active"] == 2
    assert unit["pending_terminal"] == 1
    assert 550 <= unit["oldest_pending_age_seconds"] <= 650
