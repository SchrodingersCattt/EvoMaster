"""Bohrium 作业状态表 DAO。

本模块是 bohrium_jobs 的唯一写入口，集中封装状态不变量：
- 活跃态恒有 next_poll_at、terminal_at 为 NULL；终态反之。
- 单调性：终态不被平台 poll 回退。
- 所有调度时间用 DB NOW() 计算，不在 Python 侧算。
业务代码不得裸写 status / next_poll_at / terminal_at / handled_at。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from matmaster.bohrium.status import (
    LEDGER_ACTIVE_STATUSES,
    LEDGER_FAILURE_STATUSES,
    LEDGER_TERMINAL_STATUSES,
)
from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


def _sql_status_set(statuses: Sequence[str]) -> str:
    return ", ".join(f"'{s}'" for s in statuses)


_SQL_TERMINAL = _sql_status_set(LEDGER_TERMINAL_STATUSES)
_SQL_ACTIVE = _sql_status_set(LEDGER_ACTIVE_STATUSES)
_SQL_FAILURE = _sql_status_set(LEDGER_FAILURE_STATUSES)


def _format_ts(v: Any) -> str | None:
    return v.strftime("%Y-%m-%d %H:%M:%S") if v is not None else None


class BohriumJobsTable(BaseTable):
    """bohrium_jobs DAO（raw SQL，同步 PyMySQL）。

    建表走外部脚本 src/sql/create_bohrium_jobs_table.sql；init_table 仅检查存在性。
    """

    table_name = "bohrium_jobs"
    _AGENT_COLUMNS = (
        "job_id, job_name, status, sandbox, project_id, input_dir, workspace, "
        "submitted_at, last_polled_at, result_dir"
    )
    _CLAIM_COLUMNS = (
        "id, session_id, user_id, org_id, project_id, job_id, sandbox, "
        "workspace, status, poll_count"
    )

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
        workspace: str,
    ) -> None:
        """job/add 成功后写入。next_poll_at = submitted_at（新作业即到期）。

        workspace 由唯一调用方 bohrium_jobs_wiring 归一化，DB CHECK 兜底。
        """
        if project_id is None or int(project_id) <= 0:
            raise ValueError(f"bohrium_jobs.project_id must be > 0, got {project_id!r}")
        sql = f"""
            INSERT INTO {self.table_name}
                (session_id, invocation_id, spawn_id, user_id, org_id,
                 job_id, job_name, project_id, sandbox, input_dir, workspace,
                 status, poll_count, submitted_at, next_poll_at)
            VALUES
                (%s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s, %s,
                 'submitted', 0, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                session_id = VALUES(session_id),
                invocation_id = VALUES(invocation_id),
                spawn_id = VALUES(spawn_id),
                job_name = VALUES(job_name),
                project_id = VALUES(project_id),
                input_dir = VALUES(input_dir),
                workspace = VALUES(workspace)
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
                        workspace,
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
                    WHEN status IN ({_SQL_TERMINAL})
                    THEN terminal_at
                    WHEN %s THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at
                END,
                next_poll_at = CASE
                    WHEN status IN ({_SQL_TERMINAL})
                    THEN NULL
                    WHEN %s THEN NULL
                    ELSE NOW() + INTERVAL %s SECOND
                END,
                status = CASE
                    WHEN status IN ({_SQL_TERMINAL})
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
                    WHEN status IN ({_SQL_TERMINAL})
                    THEN status ELSE 'terminating' END,
                next_poll_at = CASE
                    WHEN status IN ({_SQL_TERMINAL})
                    THEN next_poll_at ELSE COALESCE(next_poll_at, NOW()) END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, 1 if sandbox else 0, job_id))
            conn.commit()

    @staticmethod
    def _to_agent_job(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": str(row["job_id"]),
            "job_name": row["job_name"],
            "status": row["status"],
            "sandbox": bool(row["sandbox"]),
            "project_id": int(row["project_id"]),
            "input_dir": row["input_dir"],
            "workspace": row["workspace"],
            "submitted_at": _format_ts(row["submitted_at"]),
            "last_polled_at": _format_ts(row["last_polled_at"]),
            "result_dir": row["result_dir"],
        }

    def query_session_active(
        self, *, user_id: str, org_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._AGENT_COLUMNS} FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND status IN ({_SQL_ACTIVE})
            ORDER BY submitted_at ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id))
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

    def mark_poll_error(
        self,
        *,
        user_id: str,
        org_id: str,
        sandbox: bool,
        job_id: str,
        backoff_seconds: int,
        lost_after_seconds: int,
    ) -> None:
        """poll/同步失败时：活跃作业标 unknown、计数并按 backoff 推进；连续失联
        （自上次成功 poll，无则自提交）超过 lost_after_seconds 的活跃作业原子置
        终态 lost——停表、补 terminal_at、进入交付队列。

        MySQL 的 UPDATE SET 从左到右生效：status 赋值必须放最后，前列的 CASE
        才能读到旧 status（与 apply_poll 同一模式）。
        """
        sql = f"""
            UPDATE {self.table_name}
            SET
                poll_count = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN poll_count + 1 ELSE poll_count END,
                terminal_at = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN COALESCE(terminal_at, NOW())
                    ELSE terminal_at END,
                next_poll_at = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN NULL
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN NOW() + INTERVAL %s SECOND
                    ELSE next_poll_at END,
                status = CASE
                    WHEN status IN ({_SQL_ACTIVE})
                         AND NOW() > COALESCE(last_polled_at, submitted_at)
                             + INTERVAL %s SECOND
                    THEN 'lost'
                    WHEN status IN ({_SQL_ACTIVE})
                    THEN 'unknown'
                    ELSE status END
            WHERE user_id = %s AND org_id = %s AND sandbox = %s AND job_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        int(lost_after_seconds),
                        int(lost_after_seconds),
                        int(backoff_seconds),
                        int(lost_after_seconds),
                        user_id,
                        org_id,
                        1 if sandbox else 0,
                        job_id,
                    ),
                )
            conn.commit()

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

    def scan_delivery_units(self, *, limit: int) -> list[dict[str, Any]]:
        """交付聚合扫描：逐 (owner, session, invocation) 统计，仅含 pending>0 单元。

        最老 pending 优先（防饥饿）；半连接先锁定有 pending 行的单元，聚合与
        sessions EXISTS 只跑这些单元的行（表只增不删，全表聚合成本无上界）。
        EXISTS 在 SQL 层滤掉 owner 与当前 session row 不一致的行（org 切换/
        脏数据），否则它们会永久占据队首。
        """
        sql = f"""
            SELECT
                t.user_id,
                t.org_id,
                t.session_id,
                COALESCE(t.invocation_id, '')                        AS invocation_key,
                MIN(t.workspace)                                     AS workspace,
                COUNT(*)                                             AS total,
                SUM(t.terminal_at IS NULL)                           AS active,
                SUM(t.terminal_at IS NOT NULL
                    AND t.handled_at IS NULL)                        AS pending_terminal,
                SUM(t.status IN ({_SQL_FAILURE}))                    AS failed_total,
                SUM(t.status IN ({_SQL_FAILURE})
                    AND t.handled_at IS NOT NULL)                    AS failed_handled,
                SUM(t.status = 'finished')                           AS succeeded,
                SUM(t.status = 'unknown')                            AS unknown_count,
                TIMESTAMPDIFF(SECOND,
                    MIN(CASE WHEN t.terminal_at IS NOT NULL
                             AND t.handled_at IS NULL
                        THEN t.terminal_at END),
                    NOW())                                           AS oldest_pending_age_seconds,
                MAX(t.terminal_at)                                   AS max_terminal_at,
                MAX(CASE WHEN t.terminal_at IS NOT NULL AND t.handled_at IS NULL
                         THEN t.id END)                              AS max_pending_terminal_id,
                MIN(CASE WHEN t.terminal_at IS NOT NULL AND t.handled_at IS NULL
                         THEN t.terminal_at END)                     AS first_pending_terminal_at
            FROM {self.table_name} t
            JOIN (
                SELECT DISTINCT user_id, org_id, session_id,
                       COALESCE(invocation_id, '') AS invocation_key
                FROM {self.table_name}
                WHERE terminal_at IS NOT NULL AND handled_at IS NULL
            ) pending
              ON pending.user_id        = t.user_id
             AND pending.org_id         = t.org_id
             AND pending.session_id     = t.session_id
             AND pending.invocation_key = COALESCE(t.invocation_id, '')
            WHERE EXISTS (
                SELECT 1 FROM evo_chat_sessions s
                WHERE s.session_id = t.session_id
                  AND s.user_id    = t.user_id
                  AND s.org_id     = t.org_id
            )
            GROUP BY t.user_id, t.org_id, t.session_id, COALESCE(t.invocation_id, '')
            HAVING pending_terminal > 0
            ORDER BY first_pending_terminal_at ASC, t.user_id ASC, t.org_id ASC,
                     t.session_id ASC, invocation_key ASC
            LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (int(limit),))
                rows = cur.fetchall()
        return [self._to_delivery_unit(r) for r in rows]

    @staticmethod
    def _to_delivery_unit(row: dict[str, Any]) -> dict[str, Any]:
        # PyMySQL 下 SUM 返回 Decimal，统一转 int；HAVING 保证 max_id 非 NULL
        return {
            "user_id": str(row["user_id"]),
            "org_id": str(row["org_id"]),
            "session_id": str(row["session_id"]),
            "invocation_key": str(row["invocation_key"]),
            "workspace": row["workspace"],
            "total": int(row["total"]),
            "active": int(row["active"]),
            "pending_terminal": int(row["pending_terminal"]),
            "failed_total": int(row["failed_total"]),
            "failed_handled": int(row["failed_handled"]),
            "succeeded": int(row["succeeded"]),
            "unknown_count": int(row["unknown_count"]),
            "oldest_pending_age_seconds": int(row["oldest_pending_age_seconds"]),
            "max_terminal_at": row["max_terminal_at"],
            "max_pending_terminal_id": int(row["max_pending_terminal_id"]),
            "first_pending_terminal_at": row["first_pending_terminal_at"],
        }

    def list_pending_terminal_snapshot(
        self, *, user_id: str, org_id: str, session_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        """本轮 delivery 的权威集合：全量 pending terminal 行，失败/停止优先。

        无 limit——查询执行瞬间即交付边界；字段 = _AGENT_COLUMNS +
        id/invocation_id/terminal_at，保证换源不造成字段回归。
        """
        sql = f"""
            SELECT id, invocation_id, terminal_at, {self._AGENT_COLUMNS}
            FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND workspace = %s
              AND terminal_at IS NOT NULL AND handled_at IS NULL
            ORDER BY
                (status IN ({_SQL_FAILURE})) DESC,
                terminal_at ASC, submitted_at ASC, id ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, workspace))
                rows = cur.fetchall()
        return [self._to_snapshot_job(r) for r in rows]

    @classmethod
    def _to_snapshot_job(cls, row: dict[str, Any]) -> dict[str, Any]:
        job = cls._to_agent_job(row)
        job["id"] = int(row["id"])
        job["invocation_id"] = row["invocation_id"]
        job["terminal_at"] = _format_ts(row["terminal_at"])
        return job

    def mark_handled_by_ids(
        self,
        *,
        user_id: str,
        org_id: str,
        session_id: str,
        workspace: str,
        row_ids: Sequence[int],
        chunk_size: int = 500,
    ) -> int:
        """按 snapshot row ids 批量 ack；幂等（handled_at IS NULL 谓词）。

        返回实际更新行数；分块单事务提交。
        """
        ids = [int(i) for i in row_ids]
        if not ids:
            return 0
        affected = 0
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(ids), int(chunk_size)):
                    chunk = ids[start : start + int(chunk_size)]
                    placeholders = ", ".join(["%s"] * len(chunk))
                    cur.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET handled_at = NOW()
                        WHERE user_id = %s AND org_id = %s AND session_id = %s
                          AND workspace = %s
                          AND id IN ({placeholders})
                          AND terminal_at IS NOT NULL
                          AND handled_at IS NULL
                        """,
                        (user_id, org_id, session_id, workspace, *chunk),
                    )
                    affected += cur.rowcount
            conn.commit()
        return affected

    def mark_handled_by_job_keys(
        self,
        *,
        user_id: str,
        org_id: str,
        session_id: str,
        workspace: str,
        job_keys: Sequence[tuple[bool, str]],
        chunk_size: int = 500,
    ) -> int:
        """按 run 内前台观察到的 (sandbox, job_id) 批量 ack；幂等。

        session_id 约束是安全闸：apply_poll 按 owner+job_id 定位、不带 session，
        跨会话查询写终态到他会话的行，但 ack 只清本会话行，他会话应得的唤醒
        一个不少。返回实际更新行数；分块单事务提交。
        """
        keys = [(1 if sandbox else 0, str(job_id)) for sandbox, job_id in job_keys]
        if not keys:
            return 0
        affected = 0
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(keys), int(chunk_size)):
                    chunk = keys[start : start + int(chunk_size)]
                    placeholders = ", ".join(["(%s, %s)"] * len(chunk))
                    flat = [v for pair in chunk for v in pair]
                    cur.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET handled_at = NOW()
                        WHERE user_id = %s AND org_id = %s AND session_id = %s
                          AND workspace = %s
                          AND (sandbox, job_id) IN ({placeholders})
                          AND terminal_at IS NOT NULL
                          AND handled_at IS NULL
                        """,
                        (user_id, org_id, session_id, workspace, *flat),
                    )
                    affected += cur.rowcount
            conn.commit()
        return affected

    def get_first_pending_failed(
        self, *, user_id: str, org_id: str, session_id: str, invocation_key: str
    ) -> dict[str, Any] | None:
        """该 invocation 最早一个未交付失败作业（FIRST_FAILURE prompt 用）。"""
        sql = f"""
            SELECT job_id, job_name, status
            FROM {self.table_name}
            WHERE user_id = %s AND org_id = %s AND session_id = %s
              AND COALESCE(invocation_id, '') = %s
              AND status IN ({_SQL_FAILURE})
              AND handled_at IS NULL
            ORDER BY terminal_at ASC, id ASC
            LIMIT 1
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, org_id, session_id, invocation_key))
                return cur.fetchone()

    def list_all_for_test(self) -> list[dict[str, Any]]:
        """仅供测试：返回全部行。"""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table_name} ORDER BY id ASC")
                return list(cur.fetchall())


@lru_cache(maxsize=1)
def get_bohrium_jobs_table() -> BohriumJobsTable:
    """进程级单例；构造即触发 init_table 的存在性检查，复用避免重复连库。"""
    return BohriumJobsTable()
