import logging
from datetime import date, datetime, timedelta
from typing import Dict

from src.dao.user_quota_db import UserQuotaTable
from src.models.quota import QuotaType

logger = logging.getLogger(__name__)


class UserQuotaService:
    def __init__(self, db: UserQuotaTable):
        self.db = db

    def check_and_use_quota(self, user_id: str, amount: int = 1) -> Dict:
        """
        检查并使用配额（线程安全版本）
        使用原子性操作避免并发问题
        """
        try:
            # 首先确保用户存在
            user_quota = self.db.get_user_quota(user_id)
            if not user_quota:
                if not self.db.create_user(user_id):
                    return {'code': -1, 'data': {}, 'msg': '用户创建失败'}

            # 使用原子性更新
            success, result = self.db.atomic_update_quota(user_id, amount)

            if success:
                # 记录成功日志
                logger.info(
                    f"成功使用 {amount} 次配额, 剩余{result['remaining']} 次配额"
                )
                return {
                    'code': 0,
                    'data': {
                        'success': True,
                        'user_id': user_id,
                        'used_today': result['used_today'],
                        'remaining': result['remaining'],
                        'daily_quota': result['daily_quota'],
                    },
                    'msg': f'使用成功，剩余次数: {result["remaining"]}',
                }
            else:
                if 'error' in result:
                    return {'code': -3, 'data': {}, 'msg': result}

                # 记录失败日志
                logger.info(f"配额不足, 剩余{result['remaining']} 次配额")
                return {
                    'code': -2,
                    'data': {
                        'success': False,
                        'user_id': user_id,
                        'used_today': result['used_today'],
                        'remaining': result['remaining'],
                        'daily_quota': result['daily_quota'],
                    },
                    'msg': f'今日免费次数已用完，剩余: {result["remaining"]}',
                }
        except Exception as e:
            logger.error(f"使用配额时发生错误: {e}")
            return {'code': -4, 'data': {}, 'msg': f'系统错误: {str(e)}'}

    def get_quota_info(self, user_id: str) -> Dict:
        """获取用户配额信息"""
        try:
            user_quota = self.db.get_user_quota(user_id)
            logger.info(f'user_quota = {user_quota}')
            if not user_quota:
                if not self.db.create_user(user_id):
                    return {'error': '用户创建失败'}
                user_quota = self.db.get_user_quota(user_id)
            logger.info(f'user_quota = {user_quota}')

            today = date.today()
            last_reset = user_quota['last_reset_date']
            s_quota_reset_date = user_quota.get('s_quota_reset_date')
            rate_quota = user_quota.get('rate_quota', 0)
            survey_quota = user_quota.get('survey_quota', 0)

            # 检查是否需要月度重置 survey_quota（当月有效）
            if s_quota_reset_date:
                # 超过 14 天则重置
                if (today - s_quota_reset_date) >= timedelta(days=14):
                    # 重置 survey_quota 为 0，更新 s_quota_reset_date 为今天
                    self.db.update_survey_quota(user_id, 0, today)
                    survey_quota = 0
                    s_quota_reset_date = datetime.combine(today, datetime.min.time())

            # 检查是否需要每日重置
            if last_reset < today:
                user_quota['used_today'] = 0
                user_quota['last_reset_date'] = today
                rate_quota = 0
                self.db.update_quota(user_id, 0, today)

            total_quota = user_quota['daily_quota'] + rate_quota + survey_quota

            return {
                'code': 0,
                'data': {
                    'user_id': user_id,
                    'used_today': user_quota['used_today'],
                    'remaining': total_quota - user_quota['used_today'],
                    'daily_quota': total_quota,
                    'rate_quota': rate_quota,
                    'survey_quota': survey_quota,
                    'last_reset_date': user_quota['last_reset_date'].isoformat(),
                    'will_reset_in': '明天 00:00',
                    's_quota_reset_date': s_quota_reset_date,
                },
                'msg': 'success',
            }
        except Exception as e:
            logger.error(f"获取配额信息失败: {e}")
            return {'code': -1, 'data': {}, 'msg': f'获取配额信息失败: {str(e)}'}

    def batch_check_quotas(self, user_ids: list) -> Dict:
        """批量检查多个用户的配额"""
        try:
            results = {}
            for user_id in user_ids:
                results[user_id] = self.get_quota_info(user_id)
            return {'success': True, 'results': results}
        except Exception as e:
            logger.error(f"批量检查配额失败: {e}")
            return {'error': f'批量检查失败: {str(e)}'}

    def add_reward(self, user_id: str, reward_type: str) -> Dict:
        """
        Add reward based on reward type
        - rating: Add rate_quota +3 times (reset daily)
        - survey: Add survey_quota +10 times (valid for current month)
        """
        # Ensure user exists
        user_quota = self.db.get_user_quota(user_id)
        if not user_quota:
            if not self.db.create_user(user_id):
                return {'code': -1, 'data': {}, 'msg': '用户创建失败'}

        if reward_type == 'rating':
            # Add rate_quota
            success = self.db.add_rate_quota(user_id, amount=3)
            if success:
                logger.info(f"用户 {user_id} 评分奖励成功，增加 3 次评分配额")
                return {
                    'code': 0,
                    'data': {
                        'user_id': user_id,
                        'added_quota': 3,
                        'type': QuotaType.RATE_QUOTA,
                    },
                    'msg': '评分奖励成功，已增加 3 次评分配额（每日刷新）',
                }
            else:
                return {'code': -1, 'data': None, 'msg': '增加评分配额失败'}
        elif reward_type == 'survey':
            # Add survey_quota
            success = self.db.add_survey_quota(user_id, amount=10)
            if success:
                logger.info(f"用户 {user_id} 问卷奖励成功，增加 10 次问卷配额")
                return {
                    'code': 0,
                    'data': {
                        'user_id': user_id,
                        'added_quota': 10,
                        'type': QuotaType.SURVEY_QUOTA,
                    },
                    'msg': '问卷奖励成功，已增加 10 次问卷配额（当月有效）',
                }
            else:
                return {'code': -1, 'data': None, 'msg': '增加问卷配额失败'}
        else:
            return {
                'code': -1,
                'data': None,
                'msg': f'无效的奖励类型: {reward_type}，支持的类型: rating, survey',
            }


if __name__ == '__main__':
    db = UserQuotaTable()
    db_manager = UserQuotaService(db)
    result = db_manager.check_and_use_quota(user_id='1')
