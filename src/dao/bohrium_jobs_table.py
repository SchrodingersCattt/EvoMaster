"""Bohrium 作业状态表 DAO。

本模块是 bohrium_jobs 的唯一写入口，集中封装状态不变量：
- 活跃态恒有 next_poll_at、terminal_at 为 NULL；终态反之。
- 单调性：终态不被平台 poll 回退。
- 所有调度时间用 DB NOW() 计算，不在 Python 侧算。
业务代码不得裸写 status / next_poll_at / terminal_at / handled_at。
"""

from __future__ import annotations

import logging
from typing import Any

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


class BohriumJobsTable(BaseTable):
    """bohrium_jobs DAO（raw SQL，同步 PyMySQL）。"""

    table_name = "bohrium_jobs"
    _AGENT_COLUMNS = (
        "job_id, job_name, status, sandbox, project_id, input_dir, "
        "submitted_at, last_polled_at, result_dir"
    )
    _CLAIM_COLUMNS = (
        "id, session_id, user_id, org_id, project_id, job_id, sandbox, "
        "status, poll_count"
    )

    def init_table(self) -> None:
        # 建表走外部脚本 src/sql/create_bohrium_jobs_table.sql；这里仅检查存在性。
        super().init_table()

    def insert_submitted(
        self,
        *,
        session_id: str,
        invocation_id: str | None,
        spawn_id: str | None,
        user_id: str,
        org_id: str,
        job_id: str,
        job_name: str | None,
        project_id: int,
        sandbox: bool,
        input_dir: str,
    ) -> None:
        """job/add 成功后写入。next_poll_at = submitted_at（新作业即到期）。"""
        if project_id is None or int(project_id) <= 0:
            raise ValueError(
                f"bohrium_jobs.project_id must be > 0, got {project_id!r}"
            )
        sql = f"""
            INSERT INTO {self.table_name}
                (session_id, invocation_id, spawn_id, user_id, org_id,
                 job_id, job_name, project_id, sandbox, input_dir,
                 status, poll_count, submitted_at, next_poll_at)
            VALUES
                (%s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s,
                 'submitted', 0, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                session_id = VALUES(session_id),
                invocation_id = VALUES(invocation_id),
                spawn_id = VALUES(spawn_id),
                job_name = VALUES(job_name),
                project_id = VALUES(project_id),
                input_dir = VALUES(input_dir)
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        session_id,
                        invocation_id,
                        spawn_id,
                        user_id,
                        org_id,
                        job_id,
                        job_name,
                        int(project_id),
                        1 if sandbox else 0,
                        input_dir,
                    ),
                )
            conn.commit()

    def apply_poll(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        status: str,
        is_terminal: bool,
        backoff_seconds: int,
    ) -> None:
        """poll 写回。原子保护：终态不被回退；终态停轮询、补 terminal_at。"""
        sql = f"""
            UPDATE {self.table_name}
            SET
                last_polled_at = NOW(),
                poll_count = poll_count + 1,
                terminal_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped')
                    THEN terminal_at
                    WHEN %s THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at
                END,
                next_poll_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped')
                    THEN NULL
                    WHEN %s THEN NULL
                    ELSE NOW() + INTERVAL %s SECOND
                END,
                status = CASE
                    WHEN status IN ('finished', 'failed', 'stopped')
                    THEN status ELSE %s
                END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        is_terminal,
                        is_terminal,
                        int(backoff_seconds),
                        status,
                        user_id,
                        org_id,
                        1 if sandbox else 0,
                        job_id,
                    ),
                )
            conn.commit()

    def apply_kill(
        self, *, user_id: str, org_id: str, sandbox: bool, job_id: str
    ) -> None:
        """sandbox kill 请求成功后写 terminating，保留 next_poll_at 以便确认。"""
        sql = f"""
            UPDATE {self.table_name}
            SET status = CASE
                    WHEN status IN ('finished', 'failed', 'stopped')
                    THEN status ELSE 'terminating' END,
                next_poll_at = CASE
                    WHEN status IN ('finished', 'failed', 'stopped')
                    THEN next_poll_at ELSE COALESCE(next_poll_at, NOW()) END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
            conn.commit()

    def mark_handled(
        self, *, user_id: str, org_id: str, sandbox: bool, job_id: str
    ) -> None:
        """把终态作业标记为已交付；不可逆且幂等。"""
        sql = f"""
            UPDATE {self.table_name}
            SET handled_at = COALESCE(handled_at, NOW())
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
              AND terminal_at IS NOT NULL
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
            conn.commit()

    @staticmethod
    def _to_agent_job(row: dict[str, Any]) -> dict[str, Any]:
        def _ts(v: Any) -> str | None:
            return v.strftime("%Y-%m-%d %H:%M:%S") if v is not None else None

        return {
            "job_id": str(row["job_id"]),
            "job_name": row["job_name"],
            "status": row["status"],
            "sandbox": bool(row["sandbox"]),
            "project_id": int(row["project_id"]),
            "input_dir": row["input_dir"],
            "submitted_at": _ts(row["submitted_at"]),
            "last_polled_at": _ts(row["last_polled_at"]),
            "result_dir": row["result_dir"],
        }

    def query_session_active(
        self, *, user_id: str, org_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND status IN ('submitted', 'running', 'terminating', 'unknown')
            ORDER BY submitted_at ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def query_session_pending_terminal(
        self, *, user_id: str, org_id: str, session_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """待交付队列：终态已确认且尚未 handled。"""
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY terminal_at ASC, submitted_at ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, int(limit)))
                return [self._to_agent_job(r) for r in cur.fetchall()]

    def _select_due_for_update(self, conn, *, limit: int) -> list[dict[str, Any]]:
        """在给定连接的事务内 SELECT ... FOR UPDATE SKIP LOCKED。不提交。"""
        sql = f"""
            SELECT {self._CLAIM_COLUMNS} FROM {self.table_name}
            WHERE next_poll_at <= NOW()
            ORDER BY next_poll_at ASC, id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        with conn.cursor() as cur:
            cur.execute(sql, (int(limit),))
            return list(cur.fetchall())

    def claim_due_batch(
        self, *, limit: int = 50, claim_timeout_seconds: int = 120
    ) -> list[dict[str, Any]]:
        """抢一批到期作业并把 next_poll_at 占位到未来，立即提交释放锁。"""
        with self.get_connection() as conn:
            try:
                rows = self._select_due_for_update(conn, limit=limit)
                if rows:
                    ids = [r["id"] for r in rows]
                    placeholders = ", ".join(["%s"] * len(ids))
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            UPDATE {self.table_name}
                            SET next_poll_at = NOW() + INTERVAL %s SECOND
                            WHERE id IN ({placeholders})
                            """,
                            (int(claim_timeout_seconds), *ids),
                        )
                conn.commit()
                return rows
            except BaseException:
                conn.rollback()
                raise

    def get_by_owner_job(
        self, *, user_id: str, org_id: str, sandbox: bool, job_id: str
    ) -> dict[str, Any] | None:
        sql = f"""
            SELECT * FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
                return cur.fetchone()

    def list_all_for_test(self) -> list[dict[str, Any]]:
        """仅供测试：返回全部行。"""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table_name} ORDER BY id ASC")
                return list(cur.fetchall())
