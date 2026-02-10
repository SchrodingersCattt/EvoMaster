from typing import Optional

from src.dao.user_questionnaire_db import UserQuestionnaireTable
from src.models.questionnaire import (
    QuestionnaireRequest,
    QuestionnaireSubmitData,
    QuestionnaireSubmitResponse,
)
from src.utils.user import get_email_by_user_id, get_username_by_user_id


class QuestionnaireService:
    def __init__(self, db: UserQuestionnaireTable):
        self.db = db

    def submit_questionnaire(
        self, user_id: str, questionnaire_request: QuestionnaireRequest
    ) -> QuestionnaireSubmitResponse:
        """Submit a user questionnaire and return a structured response."""
        user_name = get_username_by_user_id(user_id, business_line='matmaster')
        user_email = get_email_by_user_id(user_id, business_line='matmaster')

        questionnaire_id: Optional[int] = self.db.create_questionnaire(
            user_id=user_id,
            questionnaire_request=questionnaire_request,
            user_name=user_name,
            user_email=user_email,
        )
        if questionnaire_id is None:
            return QuestionnaireSubmitResponse(
                code=-1, msg='failed to submit questionnaire', data=None
            )

        return QuestionnaireSubmitResponse(
            code=0,
            msg='success',
            data=QuestionnaireSubmitData(
                user_id=user_id, questionnaire_id=questionnaire_id
            ),
        )
