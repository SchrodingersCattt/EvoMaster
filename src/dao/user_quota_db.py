import logging
from datetime import date
from typing import Dict, Optional, Tuple

from pymysql import Error

from src.base.base_table import BaseTable
from src.utils.nacos import get_cached_email_list, is_email_allowlisted
from src.utils.user import get_email_by_user_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class UserQuotaTable(BaseTable):
    """用户配额表"""

    table_name = 'user_quota'

    def __init__(self):
        super().__init__()

    def get_user_quota(self, user_id: str) -> Optional[Dict]:
        """获取用户配额信息"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        SELECT user_id, daily_quota, rate_quota, survey_quota, used_today, last_reset_date, s_quota_reset_date
                        FROM user_quota WHERE user_id = %s
                    ''',
                        (user_id,),
                    )
                    result = cursor.fetchone()
                    return result
        except Error as e:
            logger.error(f"获取用户配额失败: {e}")
            return None

    def create_user(self, user_id: str, daily_quota: int = 10) -> bool:
        """创建新用户"""
        try:
            user_email = get_email_by_user_id(user_id, 'matmaster')
            logger.info(f"user_email: {user_email}")
            email_list_result = get_cached_email_list(cache_ttl_seconds=30)
            logger.info(f"email_list from nacos: {email_list_result}")
            is_allowlisted = False
            if email_list_result.is_ok:
                is_allowlisted = is_email_allowlisted(
                    email=user_email, email_list=email_list_result.email_list
                )

            daily_quota = 100 if is_allowlisted else 10

            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 先检查记录是否存在
                    logger.info(f'user_id = {user_id}')
                    cursor.execute(
                        'SELECT COUNT(*) FROM user_quota WHERE user_id = %s', (user_id,)
                    )
                    logger.info(cursor.fetchone())
                    cursor.execute(
                        '''
                        INSERT IGNORE INTO user_quota
                        (user_id, daily_quota, rate_quota, survey_quota, used_today, last_reset_date, updated_at, s_quota_reset_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ''',
                        (
                            user_id,
                            daily_quota,
                            0,
                            0,
                            0,
                            date.today(),
                            date.today().replace(day=1),
                            date.today().replace(day=1),
                        ),
                    )
                    conn.commit()
                    logger.info(f"rowcount: {cursor.rowcount}")
                    return cursor.rowcount > 0
        except BaseException as e:
            logger.error(f"创建用户失败: {str(e)}")
            return False

    def update_quota(self, user_id: str, used_today: int, reset_date: date) -> bool:
        """更新用户配额"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE user_quota
                        SET used_today = %s, last_reset_date = %s, rate_quota = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''',
                        (used_today, reset_date, 0, user_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"更新配额失败: {e}")
            return False

    def reset_expired_quotas(self) -> int:
        """重置过期的配额（每日任务）"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE user_quota
                        SET used_today = 0, rate_quota = 0, last_reset_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE last_reset_date < %s
                    ''',
                        (date.today(), date.today()),
                    )
                    affected_rows = cursor.rowcount
                    conn.commit()
                    logger.info(f"重置了 {affected_rows} 个用户的配额")
                    return affected_rows
        except Error as e:
            logger.error(f"重置配额失败: {e}")
            return 0

    def atomic_update_quota(self, user_id: str, amount: int = 1) -> Tuple[bool, Dict]:
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
                        '''
                        SELECT daily_quota, rate_quota, survey_quota, used_today, last_reset_date, s_quota_reset_date
                        FROM user_quota
                        WHERE user_id = %s FOR UPDATE
                    ''',
                        (user_id,),
                    )

                    user_quota = cursor.fetchone()
                    if not user_quota:
                        conn.rollback()
                        return False, {'error': '用户不存在'}

                    today = date.today()
                    last_reset = user_quota['last_reset_date']

                    # 检查是否需要重置
                    used_today = user_quota['used_today']
                    rate_quota = user_quota.get('rate_quota', 0)
                    survey_quota = user_quota.get('survey_quota', 0)

                    # 检查是否需要每日重置
                    if last_reset < today:
                        used_today = 0
                        # Reset rate_quota if date changed
                        rate_quota = 0

                    total_quota = user_quota['daily_quota'] + rate_quota + survey_quota

                    # 检查配额是否足够
                    if used_today + amount <= total_quota:
                        # 更新配额
                        new_used = used_today + amount
                        cursor.execute(
                            '''
                            UPDATE user_quota
                            SET used_today = %s, last_reset_date = %s,
                                rate_quota = %s, survey_quota = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE user_id = %s
                        ''',
                            (new_used, today, rate_quota, survey_quota, user_id),
                        )

                        conn.commit()

                        return True, {
                            'success': True,
                            'used_today': new_used,
                            'remaining': total_quota - new_used,
                            'daily_quota': user_quota['daily_quota'],
                            'rate_quota': (
                                rate_quota if last_reset >= today else 0
                            ),  # 返回的都是0，暂时用不到，后面删除掉吧
                            'survey_quota': survey_quota,
                        }
                    else:
                        conn.rollback()
                        return False, {
                            'success': False,
                            'used_today': used_today,
                            'remaining': total_quota - used_today,
                            'daily_quota': user_quota['daily_quota'],
                            'rate_quota': rate_quota if last_reset >= today else 0,
                            'survey_quota': survey_quota,
                        }

        except Error as e:
            logger.error(f"原子性更新配额失败: {e}")
            if conn:
                conn.rollback()
            return False, {'error': f'数据库错误: {str(e)}'}

    def add_rate_quota(self, user_id: str, amount: int = 3) -> bool:
        """
        Add rate_quota for user (daily reset)
        This will be reset daily
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE user_quota
                        SET rate_quota = rate_quota + %s, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''',
                        (amount, user_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"增加评分配额失败: {e}")
            return False

    def add_survey_quota(self, user_id: str, amount: int = 10) -> bool:
        """
        Add survey_quota for user (monthly reset)
        This will be reset monthly based on s_quota_reset_date
        Update s_quota_reset_date to today when adding survey quota
        """
        try:
            today = date.today()
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE user_quota
                        SET survey_quota = survey_quota + %s, s_quota_reset_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''',
                        (amount, today, user_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"增加问卷配额失败: {e}")
            return False

    def update_survey_quota(
        self, user_id: str, survey_quota: int, s_quota_reset_date: date
    ) -> bool:
        """
        Update survey_quota and s_quota_reset_date for a user
        Only handles database update, no business logic
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE user_quota
                        SET survey_quota = %s, s_quota_reset_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''',
                        (survey_quota, s_quota_reset_date, user_id),
                    )
                    conn.commit()
                    return cursor.rowcount > 0
        except Error as e:
            logger.error(f"更新用户 {user_id} 的问卷配额失败: {e}")
            return False


if __name__ == '__main__':
    db = UserQuotaTable()
    db.create_user('2')
