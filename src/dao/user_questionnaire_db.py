import json
import logging
from typing import Optional

from pymysql import Error

from src.base.base_table import BaseTable
from src.models.questionnaire import QuestionnaireRequest

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class UserQuestionnaireTable(BaseTable):
    """用户问卷表（支持多个表：ab, c, d）"""

    table_name = 'user_questionnaire_ab'  # 默认表名

    def __init__(self):
        self._table_names = {
            'ab': 'user_questionnaire_ab',
            'c': 'user_questionnaire_c',
            'd': 'user_questionnaire_d',
        }
        super().__init__()

    def init_table(self):
        """Check whether questionnaire tables exist."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    for table_name in self._table_names.values():
                        cursor.execute(
                            """
                            SELECT COUNT(*) as count FROM information_schema.tables
                            WHERE table_schema = %s AND table_name = %s
                            """,
                            (self.db_config['database'], table_name),
                        )
                        result = cursor.fetchone()
                        if result['count'] == 0:
                            logger.warning(
                                f"{table_name} table does not exist, please create it first"
                            )
                conn.commit()
        except BaseException as exc:
            logger.error(f"Questionnaire DB init failed: {exc}")
            raise

    def _get_target_table_name(self, *, recommendation_rate: str) -> Optional[str]:
        """Resolve target table name by recommendation_rate."""
        rate = (recommendation_rate or '').strip().upper()
        if rate in {'A', 'B'}:
            return self._table_names['ab']
        if rate == 'C':
            return self._table_names['c']
        if rate == 'D':
            return self._table_names['d']
        return None

    def _json_dumps_array(self, value: list[str]) -> str:
        """Serialize a list[str] into a JSON array string."""
        return json.dumps(value, ensure_ascii=False)

    def create_questionnaire(
        self,
        user_id: str,
        questionnaire_request: QuestionnaireRequest,
        user_name: str = '',
        user_email: Optional[str] = None,
    ) -> Optional[int]:
        """Insert a new questionnaire record and return the inserted ID."""
        target_table = self._get_target_table_name(
            recommendation_rate=questionnaire_request.recommendation_rate
        )

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    insert_sql = f"""
                        INSERT INTO {target_table} (
                            user_id,
                            user_name,
                            user_email,
                            recommendation_rate,
                            core_reason,
                            sub_reason,
                            practical_scenario,
                            optimization_suggestion,
                            subject_field,
                            main_functions,
                            join_group,
                            wechat,
                            contact_info
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    join_group_value = 1 if questionnaire_request.join_group else 0
                    cursor.execute(
                        insert_sql,
                        (
                            user_id,
                            user_name,
                            user_email,
                            questionnaire_request.recommendation_rate,
                            self._json_dumps_array(questionnaire_request.core_reason),
                            self._json_dumps_array(questionnaire_request.sub_reason),
                            self._json_dumps_array(
                                questionnaire_request.practical_scenario
                            ),
                            self._json_dumps_array(
                                questionnaire_request.optimization_suggestion
                            ),
                            self._json_dumps_array(questionnaire_request.subject_field),
                            self._json_dumps_array(
                                questionnaire_request.main_functions
                            ),
                            join_group_value,
                            questionnaire_request.wechat,
                            questionnaire_request.contact_info,
                        ),
                    )
                    conn.commit()
                    return cursor.lastrowid
        except Error as e:
            logger.error(
                f"Failed to insert questionnaire into {target_table} for user {user_id}: {e}"
            )
            return None
