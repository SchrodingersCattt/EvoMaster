"""飞书 open_id 与平台 user_id 绑定表。"""

import logging

from pymysql import Error

from src.base.base_table import BaseTable

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class FeishuUserBindingTable(BaseTable):
    """evo_feishu_user_binding"""

    table_name = 'evo_feishu_user_binding'

    def get_user_id_by_open_id(self, feishu_open_id: str) -> str | None:
        oid = (feishu_open_id or '').strip()
        if not oid:
            return None
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT user_id FROM {self.table_name}
                    WHERE feishu_open_id = %s
                    LIMIT 1
                    """,
                    (oid,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return str(row['user_id']) if row.get('user_id') else None

    def upsert_binding(self, feishu_open_id: str, user_id: str) -> bool:
        oid = (feishu_open_id or '').strip()
        uid = (user_id or '').strip()
        if not oid or not uid:
            return False
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {self.table_name} (feishu_open_id, user_id, created_at, updated_at)
                        VALUES (%s, %s, NOW(), NOW())
                        ON DUPLICATE KEY UPDATE
                            user_id = VALUES(user_id),
                            updated_at = NOW()
                        """,
                        (oid, uid),
                    )
                conn.commit()
                return True
        except Error as e:
            logger.warning('feishu upsert_binding failed: %s', e)
            return False

    def delete_for_user(self, user_id: str) -> bool:
        uid = (user_id or '').strip()
        if not uid:
            return False
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'DELETE FROM {self.table_name} WHERE user_id = %s',
                        (uid,),
                    )
                    n = cursor.rowcount
                conn.commit()
                return n >= 0
        except Error as e:
            logger.warning('feishu delete_for_user failed: %s', e)
            return False


def get_feishu_binding_table() -> FeishuUserBindingTable:
    return FeishuUserBindingTable()
