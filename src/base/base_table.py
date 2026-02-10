import logging
from abc import ABC
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import pymysql
from pymysql import Error

from src.utils.constant import DB_CONFIG

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BaseTable(ABC):
    """
    数据库表的基类

    所有表类都应该继承此类，并通过类属性 `table_name` 定义表名。
    子类可以重写 `init_table()` 方法来实现自定义的表初始化逻辑。
    """

    # 子类必须定义此属性
    table_name: str = None

    def __init__(self):
        # 检查子类是否定义了 table_name
        if self.table_name is None:
            raise ValueError(f"{self.__class__.__name__} 必须定义类属性 'table_name'")

        self.db_config = DB_CONFIG
        self.init_table()

    @contextmanager
    def get_connection(self):
        """获取数据库连接的上下文管理器"""
        conn = None
        try:
            conn = pymysql.connect(**self.db_config)
            yield conn
        except BaseException as e:
            logger.error(f"数据库连接错误: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def init_table(self):
        """
        初始化数据库表（如果不存在）

        子类可以重写此方法来实现自定义的表初始化逻辑，
        例如检查多个表或执行额外的验证。
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 检查表是否存在
                    cursor.execute(
                        """
                        SELECT COUNT(*) as count FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = %s
                        """,
                        (self.db_config['database'], self.table_name),
                    )

                    result = cursor.fetchone()
                    if result['count'] == 0:
                        logger.warning(
                            f'{self.table_name} 表不存在，请先运行SQL脚本创建表结构'
                        )

                conn.commit()
        except BaseException as e:
            logger.error(f"{self.__class__.__name__} 初始化失败: {e}")
            raise

    # ========== 通用 CRUD 方法（可选使用）==========

    def find_one(
        self,
        where: Dict[str, Any],
        columns: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        查询单条记录

        Args:
            where: 查询条件字典，例如 {'id': 1, 'status': 'active'}
            columns: 要查询的列名列表，None 表示查询所有列

        Returns:
            查询结果字典，如果不存在则返回 None
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    select_cols = ', '.join(columns) if columns else '*'
                    where_clause = ' AND '.join([f"{k} = %s" for k in where.keys()])
                    where_values = list(where.values())

                    sql = f"SELECT {select_cols} FROM {self.table_name} WHERE {where_clause} LIMIT 1"
                    cursor.execute(sql, where_values)
                    return cursor.fetchone()
        except Error as e:
            logger.error(f"查询失败 ({self.table_name}): {e}")
            return None

    def find_many(
        self,
        where: Optional[Dict[str, Any]] = None,
        columns: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        查询多条记录

        Args:
            where: 查询条件字典，None 表示查询所有记录
            columns: 要查询的列名列表，None 表示查询所有列
            order_by: 排序字段，例如 'created_at DESC'
            limit: 限制返回数量

        Returns:
            查询结果列表
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    select_cols = ', '.join(columns) if columns else '*'
                    sql = f"SELECT {select_cols} FROM {self.table_name}"

                    params = []
                    if where:
                        where_clause = ' AND '.join([f"{k} = %s" for k in where.keys()])
                        sql += f" WHERE {where_clause}"
                        params.extend(where.values())

                    if order_by:
                        sql += f" ORDER BY {order_by}"

                    if limit:
                        sql += f" LIMIT {limit}"

                    cursor.execute(sql, params)
                    return cursor.fetchall()
        except Error as e:
            logger.error(f"查询失败 ({self.table_name}): {e}")
            return []

    def insert(
        self,
        data: Dict[str, Any],
        ignore_duplicate: bool = False,
    ) -> Tuple[bool, Optional[int]]:
        """
        插入单条记录

        Args:
            data: 要插入的数据字典
            ignore_duplicate: 如果为 True，使用 INSERT IGNORE

        Returns:
            (是否成功, 插入的记录ID)
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    columns = ', '.join(data.keys())
                    placeholders = ', '.join(['%s'] * len(data))
                    insert_keyword = 'INSERT IGNORE' if ignore_duplicate else 'INSERT'

                    sql = f"{insert_keyword} INTO {self.table_name} ({columns}) VALUES ({placeholders})"
                    cursor.execute(sql, list(data.values()))
                    conn.commit()

                    return True, cursor.lastrowid
        except Error as e:
            logger.error(f"插入失败 ({self.table_name}): {e}")
            return False, None

    def update(
        self,
        where: Dict[str, Any],
        data: Dict[str, Any],
    ) -> bool:
        """
        更新记录

        Args:
            where: 更新条件字典
            data: 要更新的数据字典

        Returns:
            是否成功
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
                    where_clause = ' AND '.join([f"{k} = %s" for k in where.keys()])

                    sql = f"UPDATE {self.table_name} SET {set_clause} WHERE {where_clause}"
                    params = list(data.values()) + list(where.values())

                    cursor.execute(sql, params)
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"更新失败 ({self.table_name}): {e}")
            return False

    def delete(
        self,
        where: Dict[str, Any],
    ) -> bool:
        """
        删除记录

        Args:
            where: 删除条件字典

        Returns:
            是否成功
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    where_clause = ' AND '.join([f"{k} = %s" for k in where.keys()])
                    sql = f"DELETE FROM {self.table_name} WHERE {where_clause}"

                    cursor.execute(sql, list(where.values()))
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"删除失败 ({self.table_name}): {e}")
            return False
