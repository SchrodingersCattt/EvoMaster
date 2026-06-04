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
