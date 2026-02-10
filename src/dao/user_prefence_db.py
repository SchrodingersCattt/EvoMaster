import logging

from src.base.base_table import BaseTable
from src.models.preference import (
    ClickedOnboardingModel,
    ClickedOnboardingResponse,
    DefaultTabModel,
    DefaultTabResponse,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class UserPreferenceTable(BaseTable):
    """用户偏好表"""

    table_name = 'user_preference'

    def __init__(self):
        super().__init__()

    # 创建用户记录
    def create_user(self, user_id: str) -> bool:
        """创建新用户"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 先检查记录是否存在
                    logger.info(f'user_id = {user_id}')
                    cursor.execute(
                        f'SELECT COUNT(*) FROM {self.table_name} WHERE user_id = %s',
                        (user_id,),
                    )
                    logger.info(cursor.fetchone())

                    cursor.execute(
                        f'''
                        INSERT IGNORE INTO {self.table_name}
                        (user_id, default_tab_id, has_clicked_onboarding)
                        VALUES (%s, %s, %s)
                    ''',
                        (user_id, 3, False),
                    )
                    conn.commit()
                    logger.info(f"rowcount: {cursor.rowcount}")
                    return cursor.rowcount > 0
        except BaseException as e:
            logger.error(f"创建用户失败: {str(e)}")
            return False

    # 获取用户默认 Tab
    def get_user_default_tab(self, user_id: str) -> DefaultTabResponse:
        """获取用户配额信息"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        SELECT user_id, default_tab_id
                        FROM {self.table_name} WHERE user_id = %s
                    ''',
                        (user_id,),
                    )
                    result = cursor.fetchone()
                    if not result:
                        return DefaultTabResponse(
                            code=-9999, data=None, msg='用户不存在'
                        )
                    return DefaultTabResponse(
                        data=DefaultTabModel(
                            user_id=user_id, default_tab_id=result['default_tab_id']
                        )
                    )
        except BaseException as e:
            logger.error(f"获取用户偏好失败: {e}")
            return DefaultTabResponse(code=-1, data=None, msg=f"获取用户偏好失败: {e}")

    # 更新用户默认 Tab
    def update_user_default_tab(
        self, user_id: str, default_tab_id: int = 3
    ) -> DefaultTabResponse:
        """
        原子性更新配额（避免并发问题）
        使用事务和行锁确保数据一致性
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 开启事务
                    conn.begin()

                    # 使用 SELECT FOR UPDATE 锁定行
                    cursor.execute(
                        f'''
                        SELECT default_tab_id
                        FROM {self.table_name}
                        WHERE user_id = %s FOR UPDATE
                    ''',
                        (user_id,),
                    )

                    user_preference = cursor.fetchone()
                    if not user_preference:
                        conn.rollback()
                        return DefaultTabResponse(code=-1, data={}, msg='用户不存在')

                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET default_tab_id = %s
                        WHERE user_id = %s
                    ''',
                        (default_tab_id, user_id),
                    )

                    conn.commit()

                    return DefaultTabResponse(
                        data=DefaultTabModel(
                            user_id=user_id, default_tab_id=default_tab_id
                        )
                    )
        except BaseException as e:
            logger.error(f"原子性更新配额失败: {e}")
            if conn:
                conn.rollback()

            return DefaultTabResponse(code=-1, data={}, msg=f'数据库错误: {str(e)}')

    # 确认用户是否点击了新手指引
    def get_user_clicked_onboarding(self, user_id: str) -> ClickedOnboardingResponse:
        """获取用户配额信息"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f'''
                        SELECT user_id, has_clicked_onboarding
                        FROM {self.table_name} WHERE user_id = %s
                    ''',
                        (user_id,),
                    )
                    result = cursor.fetchone()
                    if not result:
                        return ClickedOnboardingResponse(
                            code=-9999, data=None, msg='用户不存在'
                        )
                    return ClickedOnboardingResponse(
                        data=ClickedOnboardingModel(
                            user_id=user_id,
                            has_clicked_onboarding=result['has_clicked_onboarding'],
                        )
                    )
        except BaseException as e:
            logger.error(f"get_user_has_clicked_onboarding: {e}")
            return ClickedOnboardingResponse(
                code=-1, data=None, msg=f"get_user_has_clicked_onboarding: {e}"
            )

    # 更新用户默认 Tab
    def update_user_clicked_onboarding(self, user_id: str) -> ClickedOnboardingResponse:
        """
        原子性更新配额（避免并发问题）
        使用事务和行锁确保数据一致性
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 开启事务
                    conn.begin()

                    # 使用 SELECT FOR UPDATE 锁定行
                    cursor.execute(
                        f'''
                        SELECT has_clicked_onboarding
                        FROM {self.table_name}
                        WHERE user_id = %s FOR UPDATE
                    ''',
                        (user_id,),
                    )

                    user_has_clicked_onboarding = cursor.fetchone()
                    if not user_has_clicked_onboarding:
                        conn.rollback()
                        return ClickedOnboardingResponse(
                            code=-1, data={}, msg='用户不存在'
                        )

                    cursor.execute(
                        f'''
                        UPDATE {self.table_name}
                        SET has_clicked_onboarding = %s
                        WHERE user_id = %s
                    ''',
                        (True, user_id),
                    )

                    conn.commit()

                    return ClickedOnboardingResponse(
                        data=ClickedOnboardingModel(
                            user_id=user_id, has_clicked_onboarding=True
                        )
                    )
        except BaseException as e:
            logger.error(f"原子性更新配额失败: {e}")
            if conn:
                conn.rollback()

            return ClickedOnboardingResponse(
                code=-1, data={}, msg=f'数据库错误: {str(e)}'
            )
