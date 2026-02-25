"""Bohrium 节点复用表：按 user_id + org_id + project_id 查询/插入/更新 last_used_at。"""

import logging
from functools import lru_cache
from typing import Any

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


class BohriumNodesTable(BaseTable):
    """evo_bohrium_nodes：可复用节点缓存。"""

    table_name = 'evo_bohrium_nodes'

    def find_one_for_reuse(
        self, user_id: str, org_id: str, project_id: int
    ) -> dict[str, Any] | None:
        """取一条可复用的节点（同一 user/org/project），按 last_used_at 倒序取最新。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    SELECT id, user_id, org_id, project_id, node_id, last_used_at, created_at, updated_at
                    FROM {self.table_name}
                    WHERE user_id = %s AND org_id = %s AND project_id = %s
                    ORDER BY last_used_at DESC
                    LIMIT 1
                    ''',
                    (user_id, org_id, project_id),
                )
                return cursor.fetchone()

    def insert_node(
        self,
        user_id: str,
        org_id: str,
        project_id: int,
        node_id: int,
    ) -> bool:
        """插入一条节点记录。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    INSERT INTO {self.table_name}
                    (user_id, org_id, project_id, node_id, last_used_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW(), NOW())
                    ''',
                    (user_id, org_id, project_id, node_id),
                )
                conn.commit()
                logger.info(
                    'bohrium_nodes_table: inserted user_id=%s org_id=%s project_id=%s node_id=%s',
                    user_id,
                    org_id,
                    project_id,
                    node_id,
                )
                return cursor.rowcount > 0

    def update_last_used_at(
        self, user_id: str, org_id: str, project_id: int, node_id: int
    ) -> bool:
        """更新该节点的 last_used_at 为当前时间。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    UPDATE {self.table_name}
                    SET last_used_at = NOW(), updated_at = NOW()
                    WHERE user_id = %s AND org_id = %s AND project_id = %s AND node_id = %s
                    ''',
                    (user_id, org_id, project_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_by_node(
        self, user_id: str, org_id: str, project_id: int, node_id: int
    ) -> bool:
        """删除一条节点记录（节点已销毁或不可用时）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f'''
                    DELETE FROM {self.table_name}
                    WHERE user_id = %s AND org_id = %s AND project_id = %s AND node_id = %s
                    ''',
                    (user_id, org_id, project_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0


@lru_cache
def get_bohrium_nodes_table() -> BohriumNodesTable:
    return BohriumNodesTable()
