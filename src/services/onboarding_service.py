import logging

from src.dao.user_prefence_db import UserPreferenceTable
from src.models.preference import ClickedOnboardingResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class OnboardingService:
    def __init__(self, db: UserPreferenceTable):
        self.db = db

    def get_user_clicked_onboarding(self, user_id: str) -> ClickedOnboardingResponse:
        user_clicked_onboarding = self.db.get_user_clicked_onboarding(user_id)
        if user_clicked_onboarding.code == -9999:
            if not self.db.create_user(user_id):
                return ClickedOnboardingResponse(code=-1, data=None, msg='用户创建失败')
            user_clicked_onboarding = self.db.get_user_clicked_onboarding(user_id)
        logger.info(f'user_clicked_onboarding = {user_clicked_onboarding}')

        return ClickedOnboardingResponse(
            code=user_clicked_onboarding.code,
            data=user_clicked_onboarding.data,
            msg=user_clicked_onboarding.msg,
        )

    def check_and_set_clicked_onboarding(
        self, user_id: str
    ) -> ClickedOnboardingResponse:
        # 首先确保用户存在
        user_clicked_onboarding = self.db.get_user_clicked_onboarding(user_id)
        if not user_clicked_onboarding:
            if not self.db.create_user(user_id):
                return ClickedOnboardingResponse(code=-1, data=None, msg='用户创建失败')

        # 使用原子性更新
        result = self.db.update_user_clicked_onboarding(user_id)

        return ClickedOnboardingResponse(
            code=result.code, data=result.data, msg=result.msg
        )
