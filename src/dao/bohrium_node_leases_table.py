"""Invocation-scoped leases for shared Bohrium node slots."""

from functools import lru_cache
from typing import Any

from src.base.base_table import BaseTable


class BohriumNodeLeasesTable(BaseTable):
    """bohrium_node_leases：带 fencing token 的共享租约。"""

    table_name = "bohrium_node_leases"

    def acquire(
        self,
        node_slot_id: int,
        session_id: str,
        invocation_id: str,
        lease_token: str,
        lease_ttl_seconds: int,
    ) -> bool:
        """创建 lease；同 invocation 重试会换 token，旧 Worker 随即失去写权限。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self.table_name}
                    (node_slot_id, session_id, invocation_id, lease_token,
                     lease_expires_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s,
                            DATE_ADD(NOW(), INTERVAL %s SECOND), NOW(), NOW())
                    ON DUPLICATE KEY UPDATE
                        node_slot_id = VALUES(node_slot_id),
                        session_id = VALUES(session_id),
                        lease_token = VALUES(lease_token),
                        lease_expires_at = VALUES(lease_expires_at),
                        updated_at = NOW()
                    """,
                    (
                        node_slot_id,
                        session_id,
                        invocation_id,
                        lease_token,
                        lease_ttl_seconds,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0

    def heartbeat(
        self, invocation_id: str, lease_token: str, lease_ttl_seconds: int
    ) -> bool:
        """只允许当前 token 延长租约。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET lease_expires_at = DATE_ADD(NOW(), INTERVAL %s SECOND),
                        updated_at = NOW()
                    WHERE invocation_id = %s AND lease_token = %s
                    """,
                    (lease_ttl_seconds, invocation_id, lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def release(self, invocation_id: str, lease_token: str) -> bool:
        """只删除 invocation 当前 token 对应的 lease。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE invocation_id = %s AND lease_token = %s
                    """,
                    (invocation_id, lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def release_expired(self, invocation_id: str, lease_token: str) -> bool:
        """Recycler 专用：扫描后仍过期且 token 未变化时才删除。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE invocation_id = %s AND lease_token = %s
                      AND lease_expires_at <= NOW()
                    """,
                    (invocation_id, lease_token),
                )
                conn.commit()
                return cursor.rowcount > 0

    def list_expired(self, limit: int) -> list[dict[str, Any]]:
        """按到期时间扫描候选；删除时仍需 release_expired 二次 CAS。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, node_slot_id, session_id, invocation_id,
                           lease_token, lease_expires_at
                    FROM {self.table_name}
                    WHERE lease_expires_at <= NOW()
                    ORDER BY lease_expires_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return cursor.fetchall() or []

    def count_live(self, node_slot_id: int) -> int:
        """统计槽位仍未过期的 invocation leases。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS lease_count
                    FROM {self.table_name}
                    WHERE node_slot_id = %s AND lease_expires_at > NOW()
                    """,
                    (node_slot_id,),
                )
                row: dict[str, Any] | None = cursor.fetchone()
        return int((row or {}).get("lease_count") or 0)

    def delete_expired_for_slot(self, node_slot_id: int) -> int:
        """原子退休槽位的过期 lease；并发 heartbeat 由行锁和期限条件仲裁。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE node_slot_id = %s AND lease_expires_at <= NOW()
                    """,
                    (node_slot_id,),
                )
                conn.commit()
                return cursor.rowcount


@lru_cache
def get_bohrium_node_leases_table() -> BohriumNodeLeasesTable:
    return BohriumNodeLeasesTable()
