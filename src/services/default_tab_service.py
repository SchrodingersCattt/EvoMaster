import logging

from src.dao.user_prefence_db import UserPreferenceTable
from src.models.preference import DefaultTabResponse

logger = logging.getLogger(__name__)


class UserDefaultTabService:
    def __init__(self, db: UserPreferenceTable):
        self.db = db

    def get_user_default_tab(self, user_id: str) -> DefaultTabResponse:
        """获取用户偏好信息"""
        user_preference = self.db.get_user_default_tab(user_id)
        if user_preference.code == -9999:
            if not self.db.create_user(user_id):
                return DefaultTabResponse(code=-1, data=None, msg='用户创建失败')
            user_preference = self.db.get_user_default_tab(user_id)
        logger.info(f'user_preference = {user_preference}')

        return DefaultTabResponse(
            code=user_preference.code,
            data=user_preference.data,
            msg=user_preference.msg,
        )

    def check_and_set_default_tab(
        self, user_id: str, default_tab_id: int = 3
    ) -> DefaultTabResponse:
        """
        检查并设置用户偏好（线程安全版本）
        使用原子性操作避免并发问题
        """
        # 首先确保用户存在
        user_quota = self.db.get_user_default_tab(user_id)
        if not user_quota:
            if not self.db.create_user(user_id):
                return DefaultTabResponse(code=-1, data=None, msg='用户创建失败')

        # 使用原子性更新
        result = self.db.update_user_default_tab(user_id, default_tab_id)

        return DefaultTabResponse(code=result.code, data=result.data, msg=result.msg)
