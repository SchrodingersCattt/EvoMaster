import logging
import sys
from pathlib import Path

from pymysql import Error

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parents[1]

if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.base.base_table import BaseTable  # noqa: E402

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class InvocationStatusTable(BaseTable):
    """调用状态表"""

    table_name = 'invocation_status'

    def __init__(self):
        super().__init__()

    def update(self, invocation_id: str, status: int) -> bool:
        """
        更新或插入 invocation_id 和 invocation_status
        如果记录已存在则更新，不存在则插入
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 使用 INSERT ... ON DUPLICATE KEY UPDATE 实现 upsert
                    # 假设 invocation_id 是主键或唯一索引
                    sql = """
                        INSERT INTO invocation_status (invocation_id, invocation_status, updated_at)
                        VALUES (%s, %s, CURRENT_TIMESTAMP)
                        ON DUPLICATE KEY UPDATE
                            invocation_status = VALUES(invocation_status),
                            updated_at = CURRENT_TIMESTAMP
                    """
                    cursor.execute(sql, (invocation_id, status))
                    conn.commit()
                    logger.info(
                        f'更新 invocation 状态成功: invocation_id={invocation_id}, invocation_status={status}'
                    )
                    return True
        except Error as e:
            logger.error(f'更新 invocation 状态失败: {e}')
            return False

    def check(self, invocation_id: str) -> int:
        """
        获取 invocation_id 对应的 status
        如果记录不存在，返回 0
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                        SELECT invocation_status
                        FROM invocation_status
                        WHERE invocation_id = %s
                    """
                    cursor.execute(sql, (invocation_id,))
                    result = cursor.fetchone()
                    if result:
                        return int(result['invocation_status'])
                    return 0
        except Error as e:
            logger.error(f'查询 invocation 状态失败: {e}')
            return 0


if __name__ == '__main__':
    db = InvocationStatusTable()
    # db.update('1', 1)
    print(db.check('1'))
