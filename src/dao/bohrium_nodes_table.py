"""Bohrium 节点复用表：按 user_id + org_id + project_id + sku_id 复用节点。"""

import logging
from functools import lru_cache
from typing import Any

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)


class BohriumNodesTable(BaseTable):
    """evo_bohrium_nodes：可复用节点缓存。"""

    table_name = "evo_bohrium_nodes"

    def find_one_for_reuse(
        self, user_id: str, org_id: str, project_id: int, sku_id: int
    ) -> dict[str, Any] | None:
        """按唯一槽位读取可复用节点（同一 user/org/project/sku 最多一条）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, org_id, project_id, sku_id, node_id,
                           state, creating_invocation_id, creating_lease_token,
                           creating_lease_expires_at, lifecycle_policy,
                           idle_timeout_seconds, idle_expires_at, last_used_at,
                           created_at, updated_at
                    FROM {self.table_name}
                    WHERE user_id = %s AND org_id = %s AND project_id = %s AND sku_id = %s
                    LIMIT 1
                    """,
                    (user_id, org_id, project_id, sku_id),
                )
                return cursor.fetchone()

    def find_by_id(self, slot_id: int) -> dict[str, Any] | None:
        """按主键读取槽位及回收所需身份信息。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, org_id, project_id, sku_id, node_id,
                           state, creating_invocation_id, creating_lease_token,
                           creating_lease_expires_at, lifecycle_policy,
                           idle_timeout_seconds, idle_expires_at, last_used_at,
                           created_at, updated_at
                    FROM {self.table_name}
                    WHERE id = %s
                    LIMIT 1
                    """,
                    (slot_id,),
                )
                return cursor.fetchone()

    def insert_creating_slot(
        self,
        user_id: str,
        org_id: str,
        project_id: int,
        sku_id: int,
        invocation_id: str,
        creating_lease_token: str,
        lease_ttl_seconds: int,
    ) -> bool:
        """只在槽位不存在时插入 creating 占位，绝不覆盖已有 node_id。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {self.table_name}
                    (user_id, org_id, project_id, sku_id, state,
                     creating_invocation_id, creating_lease_token,
                     creating_lease_expires_at, lifecycle_policy,
                     last_used_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'creating', %s, %s,
                            DATE_ADD(NOW(), INTERVAL %s SECOND), 'run_end',
                            NOW(), NOW(), NOW())
                    """,
                    (
                        user_id,
                        org_id,
                        project_id,
                        sku_id,
                        invocation_id,
                        creating_lease_token,
                        lease_ttl_seconds,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_ready(self, slot_id: int, creating_lease_token: str, node_id: int) -> bool:
        """以 creation token 为 fencing 条件，将槽位发布为 ready。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET node_id = %s, state = 'ready',
                        creating_invocation_id = NULL,
                        creating_lease_token = NULL,
                        creating_lease_expires_at = NULL,
                        last_used_at = NOW(), updated_at = NOW()
                    WHERE id = %s AND state = 'creating'
                      AND creating_lease_token = %s
                    """,
                    (node_id, slot_id, creating_lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def set_lifecycle_policy(
        self,
        slot_id: int,
        lifecycle_policy: str,
        idle_timeout_seconds: int | None,
    ) -> bool:
        """在槽位短锁内写入最近一次成功 acquire 的 desired policy。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET lifecycle_policy = %s, idle_timeout_seconds = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND state IN ('creating', 'ready', 'paused', 'idle')
                    """,
                    (lifecycle_policy, idle_timeout_seconds, slot_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def claim_idle_for_acquire(
        self,
        slot_id: int,
        node_id: int,
        lifecycle_policy: str,
        idle_timeout_seconds: int | None,
    ) -> bool:
        """新 invocation 原子取消 idle deadline 并恢复 ready。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'ready', lifecycle_policy = %s,
                        idle_timeout_seconds = %s, idle_expires_at = NULL,
                        last_error = NULL, last_used_at = NOW(), updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'idle'
                    """,
                    (lifecycle_policy, idle_timeout_seconds, slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def attach_creating_node(
        self, slot_id: int, creating_lease_token: str, node_id: int
    ) -> bool:
        """provider create 返回后立即登记 node_id，wait-ready 前仍由 token fencing。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET node_id = %s, updated_at = NOW()
                    WHERE id = %s AND state = 'creating'
                      AND creating_lease_token = %s
                    """,
                    (node_id, slot_id, creating_lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def claim_expired_creation(
        self,
        slot_id: int,
        invocation_id: str,
        creating_lease_token: str,
        lease_ttl_seconds: int,
    ) -> bool:
        """接管已过期的 create/restart claim。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET creating_invocation_id = %s,
                        creating_lease_token = %s,
                        creating_lease_expires_at =
                            DATE_ADD(NOW(), INTERVAL %s SECOND),
                        last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND state = 'creating'
                      AND creating_lease_expires_at <= NOW()
                    """,
                    (
                        invocation_id,
                        creating_lease_token,
                        lease_ttl_seconds,
                        slot_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def begin_restart(
        self,
        slot_id: int,
        node_id: int,
        invocation_id: str,
        creating_lease_token: str,
        lease_ttl_seconds: int,
    ) -> bool:
        """将 paused 或 provider 已停止的 ready 槽位原子切到 creating。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'creating', creating_invocation_id = %s,
                        creating_lease_token = %s,
                        creating_lease_expires_at =
                            DATE_ADD(NOW(), INTERVAL %s SECOND),
                        last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND node_id = %s
                      AND state IN ('paused', 'ready')
                    """,
                    (
                        invocation_id,
                        creating_lease_token,
                        lease_ttl_seconds,
                        slot_id,
                        node_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def expire_creation(
        self, slot_id: int, creating_lease_token: str, error: str
    ) -> bool:
        """使失败 claim 可立即被接管，同时保留错误用于审计。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET creating_lease_expires_at = NOW(), last_error = %s,
                        updated_at = NOW()
                    WHERE id = %s AND state = 'creating'
                      AND creating_lease_token = %s
                    """,
                    (error[:2000], slot_id, creating_lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_stopping(self, slot_id: int, node_id: int) -> bool:
        """把无 live lease 的 ready/idle 槽位切到 stopping。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'stopping', last_used_at = NOW(),
                        last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND node_id = %s
                      AND state IN ('ready', 'idle')
                    """,
                    (slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_idle(
        self,
        slot_id: int,
        node_id: int,
        lifecycle_policy: str,
        idle_timeout_seconds: int | None,
    ) -> bool:
        """最后一个 lease 释放后将 ready 槽位转为 idle。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'idle', lifecycle_policy = %s,
                        idle_timeout_seconds = %s,
                        idle_expires_at = CASE
                            WHEN %s IS NULL THEN NULL
                            ELSE DATE_ADD(NOW(), INTERVAL %s SECOND)
                        END,
                        last_used_at = NOW(), last_error = NULL,
                        updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'ready'
                    """,
                    (
                        lifecycle_policy,
                        idle_timeout_seconds,
                        idle_timeout_seconds,
                        idle_timeout_seconds,
                        slot_id,
                        node_id,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_stopping_due_idle(self, slot_id: int, node_id: int) -> bool:
        """只 claim 仍已到期的 idle_timeout 槽位，避免旧扫描误停复用节点。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'stopping', last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'idle'
                      AND lifecycle_policy = 'idle_timeout'
                      AND idle_expires_at IS NOT NULL
                      AND idle_expires_at <= NOW()
                    """,
                    (slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_stopping_expired_creation(
        self, slot_id: int, node_id: int, creating_lease_token: str
    ) -> bool:
        """将仍由同一 token 持有且已过期的 creating 槽位切到 stopping。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'stopping', last_error = NULL,
                        updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'creating'
                      AND creating_lease_token = %s
                      AND creating_lease_expires_at <= NOW()
                    """,
                    (slot_id, node_id, creating_lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_expired_empty_creation(
        self, slot_id: int, creating_lease_token: str
    ) -> bool:
        """删除 provider 尚未返回 node_id 的过期空占位。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE id = %s AND node_id IS NULL AND state = 'creating'
                      AND creating_lease_token = %s
                      AND creating_lease_expires_at <= NOW()
                    """,
                    (slot_id, creating_lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_paused(self, slot_id: int, node_id: int) -> bool:
        """provider stop 成功后把 stopping 槽位切到 paused。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'paused', creating_invocation_id = NULL,
                        creating_lease_token = NULL,
                        creating_lease_expires_at = NULL,
                        idle_expires_at = NULL,
                        last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'stopping'
                    """,
                    (slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def mark_ready_paused(self, slot_id: int, node_id: int) -> bool:
        """provider 已停止时把仍为 ready 的历史槽位对账为 paused。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET state = 'paused', creating_invocation_id = NULL,
                        creating_lease_token = NULL,
                        creating_lease_expires_at = NULL,
                        idle_expires_at = NULL,
                        last_error = NULL, updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'ready'
                    """,
                    (slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def list_expired_creating_slots(self, limit: int) -> list[dict[str, Any]]:
        """扫描 create/restart Worker 失联或准备失败后的槽位。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, org_id, project_id, sku_id, node_id,
                           state, creating_invocation_id, creating_lease_token,
                           creating_lease_expires_at, lifecycle_policy,
                           idle_timeout_seconds, last_error, updated_at
                    FROM {self.table_name}
                    WHERE state = 'creating'
                      AND creating_lease_expires_at <= NOW()
                    ORDER BY creating_lease_expires_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cursor.fetchall() or []

    def record_stop_error(self, slot_id: int, node_id: int, error: str) -> bool:
        """保留 stopping 状态供 monitor 重试，并记录最近错误。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET last_error = %s, updated_at = NOW()
                    WHERE id = %s AND node_id = %s AND state = 'stopping'
                    """,
                    (error[:2000], slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def list_stopping_without_live_leases(
        self, limit: int, min_age_seconds: int
    ) -> list[dict[str, Any]]:
        """扫描 provider stop 失败后需要 monitor 重试的槽位。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT n.id, n.user_id, n.org_id, n.project_id, n.sku_id,
                           n.node_id, n.state, n.lifecycle_policy,
                           n.idle_timeout_seconds, n.last_error, n.updated_at
                    FROM {self.table_name} AS n
                    LEFT JOIN bohrium_node_leases AS l
                      ON l.node_slot_id = n.id
                     AND l.lease_expires_at > NOW()
                    WHERE n.state = 'stopping' AND l.id IS NULL
                      AND n.updated_at <=
                          DATE_SUB(NOW(), INTERVAL %s SECOND)
                    ORDER BY n.updated_at ASC
                    LIMIT %s
                    """,
                    (min_age_seconds, limit),
                )
                return cursor.fetchall() or []

    def list_due_idle_slots(self, limit: int) -> list[dict[str, Any]]:
        """扫描已到期 idle_timeout；NULL deadline 的 keep_running 不会命中。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, user_id, org_id, project_id, sku_id, node_id,
                           state, lifecycle_policy, idle_timeout_seconds,
                           idle_expires_at, last_error, updated_at
                    FROM {self.table_name}
                    WHERE state = 'idle'
                      AND lifecycle_policy = 'idle_timeout'
                      AND idle_expires_at IS NOT NULL
                      AND idle_expires_at <= NOW()
                    ORDER BY idle_expires_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cursor.fetchall() or []

    def list_ready_without_live_leases(self, limit: int) -> list[dict[str, Any]]:
        """只读扫描迁移前遗留的 ready 槽位，供人工运行态审计。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT n.id, n.user_id, n.org_id, n.project_id, n.sku_id,
                           n.node_id, n.state, n.last_used_at, n.updated_at
                    FROM {self.table_name} AS n
                    LEFT JOIN bohrium_node_leases AS l
                      ON l.node_slot_id = n.id
                     AND l.lease_expires_at > NOW()
                    WHERE n.state = 'ready' AND n.node_id IS NOT NULL
                      AND l.id IS NULL
                    ORDER BY n.last_used_at ASC, n.id ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cursor.fetchall() or []

    def list_node_ids_for_user_org(self, user_id: str, org_id: str) -> set[int]:
        """按 user/org 返回已登记的 node_id 集合（跨 project）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT DISTINCT node_id
                    FROM {self.table_name}
                    WHERE user_id = %s AND org_id = %s
                    """,
                    (user_id, org_id),
                )
                rows = cursor.fetchall() or []
        out: set[int] = set()
        for row in rows:
            node_id = row.get("node_id")
            try:
                out.add(int(node_id))
            except (TypeError, ValueError):
                continue
        return out

    def insert_node(
        self,
        user_id: str,
        org_id: str,
        project_id: int,
        sku_id: int,
        node_id: int,
    ) -> bool:
        """旧调用兼容：只插入空槽位，绝不覆盖已有 node_id。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {self.table_name}
                    (user_id, org_id, project_id, sku_id, node_id, state,
                     lifecycle_policy, last_used_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'ready', 'run_end',
                            NOW(), NOW(), NOW())
                    """,
                    (user_id, org_id, project_id, sku_id, node_id),
                )
                conn.commit()
                logger.info(
                    "bohrium_nodes_table: inserted legacy slot user_id=%s "
                    "org_id=%s project_id=%s sku_id=%s node_id=%s",
                    user_id,
                    org_id,
                    project_id,
                    sku_id,
                    node_id,
                )
                return cursor.rowcount > 0

    def update_last_used_at(
        self, user_id: str, org_id: str, project_id: int, sku_id: int, node_id: int
    ) -> bool:
        """更新该节点的 last_used_at 为当前时间。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET last_used_at = NOW(), updated_at = NOW()
                    WHERE user_id = %s AND org_id = %s AND project_id = %s
                      AND sku_id = %s AND node_id = %s
                    """,
                    (user_id, org_id, project_id, sku_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_by_node(
        self, user_id: str, org_id: str, project_id: int, sku_id: int, node_id: int
    ) -> bool:
        """删除一条节点记录（节点已销毁或不可用时）。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE user_id = %s AND org_id = %s AND project_id = %s
                      AND sku_id = %s AND node_id = %s
                    """,
                    (user_id, org_id, project_id, sku_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_stopping_slot(self, slot_id: int, node_id: int) -> bool:
        """仅删除仍指向目标 Node 的 stopping 槽位。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE id = %s AND node_id = %s AND state = 'stopping'
                    """,
                    (slot_id, node_id),
                )
                conn.commit()
                return cursor.rowcount > 0


@lru_cache
def get_bohrium_nodes_table() -> BohriumNodesTable:
    return BohriumNodesTable()
