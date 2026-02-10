import argparse
import importlib
import json
import logging
import os
import sys
from pathlib import Path

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parents[1]

if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.base.base_table import BaseTable  # noqa: E402
from src.utils import constant  # noqa: E402

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RecommendQuestionsTable(BaseTable):
    """推荐问题表"""

    table_name = 'recommend_questions'

    def __init__(self):
        super().__init__()

    def update(self, json_path: str):
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # 1. 清空表（注意：不会触发 auto_increment）
                    cursor.execute('TRUNCATE TABLE recommend_questions')

                    # 2. 插入所有记录
                    insert_sql = """
                        INSERT INTO recommend_questions (belonging, question, structure_url, sharing_url, figure_url, belonging_en, question_en, sharing_url_en)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """

                    with open(json_path) as f:
                        data = json.load(f)

                    questions = data.get('questions', [])

                    env_url_key_mapping = {
                        'test': 'test_url',
                        'uat': 'uat_url',
                        'prod': 'prod_url',
                    }
                    sharing_env_key = env_url_key_mapping.get(constant.CURRENT_ENV)

                    for item in questions:
                        zh = item.get('zhPrompt', {})

                        # 英文字段在 zhPrompt 下的 belonging_en / question_en
                        belonging_en = zh.get('belonging_en')
                        question_en = zh.get('question_en')

                        figure_url = zh.get('figure_url')

                        sharing_url_config = zh.get('sharing_url')
                        sharing_url = None
                        if isinstance(sharing_url_config, dict) and sharing_env_key:
                            sharing_url_candidate = sharing_url_config.get(
                                sharing_env_key
                            )
                            if sharing_url_candidate:
                                sharing_url = sharing_url_candidate

                        sharing_url_en_config = zh.get('sharing_url_en')
                        sharing_url_en = None
                        if isinstance(sharing_url_en_config, dict) and sharing_env_key:
                            sharing_url_en_candidate = sharing_url_en_config.get(
                                sharing_env_key
                            )
                            if sharing_url_en_candidate:
                                sharing_url_en = sharing_url_en_candidate

                        cursor.execute(
                            insert_sql,
                            (
                                zh.get('belonging'),
                                zh.get('question'),
                                zh.get('structure_url'),
                                sharing_url,
                                figure_url,
                                belonging_en,
                                question_en,
                                sharing_url_en,
                            ),
                        )
                conn.commit()
                logger.info(f'{self.table_name} 表刷新完成！')
        except Exception as e:
            logger.error(f"{e}")
            return None

    def get_all_belonging(self):
        """
        获取所有 distinct belonging 列表
        过滤掉 belonging 为：场景案例、学术雷达、专题研报
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                        SELECT DISTINCT belonging, belonging_en
                        FROM recommend_questions
                        WHERE belonging NOT IN (%s, %s, %s)
                    """
                    cursor.execute(sql, ('场景案例', '学术雷达', '专题研报'))
                    rows = cursor.fetchall()
                    return [
                        {
                            'belonging': item['belonging'],
                            'belonging_en': item['belonging_en'],
                        }
                        for item in rows
                    ]
        except BaseException as e:
            logger.error(f"{e}")
            return None

    def get_all(self):
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    sql = 'SELECT id, belonging, question, structure_url, sharing_url, figure_url, belonging_en, question_en, sharing_url_en FROM recommend_questions'
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    return rows
        except BaseException as e:
            logger.error(f"{e}")
            return None

    def get_by_belonging(self, belonging):
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                    SELECT id, question, structure_url, sharing_url, figure_url, belonging_en, question_en, sharing_url_en
                    FROM recommend_questions
                    WHERE belonging = %s
                    """
                    cursor.execute(sql, (belonging,))
                    rows = cursor.fetchall()
                    return rows
        except BaseException as e:
            logger.error(f"{e}")
            return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='更新推荐问题数据库')
    parser.add_argument(
        'env',
        nargs='?',
        default=None,
        help='环境变量 (test/uat/prod)，如果不指定则使用 .env 文件中的 SERVICE_ENV',
    )
    parser.add_argument(
        '--json-path',
        default='../../recommend_question.json',
        help='JSON 文件路径，默认为 ../../recommend_question.json',
    )
    args = parser.parse_args()

    # 如果指定了环境变量，设置并重新加载配置
    if args.env:
        os.environ['SERVICE_ENV'] = args.env
        # 重新加载 constant 和 base_db 模块以应用新的环境变量
        importlib.reload(constant)
        from src.base import base_table  # noqa: E402

        importlib.reload(base_db)
        # 重新导入 BaseDB 并使用它创建新的 RecommendQuestionsTable 实例
        from src.base.base_table import BaseTable as ReloadedBaseDB  # noqa: E402

        # 动态创建使用新 BaseDB 的 RecommendQuestionsTable 类
        class RecommendQuestionsTableWithEnv(ReloadedBaseDB):
            table_name = 'recommend_questions'

            def __init__(self):
                super().__init__()

        # 复制原有类的方法到新类
        RecommendQuestionsTableWithEnv.update = RecommendQuestionsTable.update
        RecommendQuestionsTableWithEnv.get_all_belonging = (
            RecommendQuestionsTable.get_all_belonging
        )
        RecommendQuestionsTableWithEnv.get_all = RecommendQuestionsTable.get_all
        RecommendQuestionsTableWithEnv.get_by_belonging = (
            RecommendQuestionsTable.get_by_belonging
        )

        db = RecommendQuestionsTableWithEnv()
    else:
        db = RecommendQuestionsTable()

    db.update(args.json_path)
    result = db.get_all_belonging()
    belonging_result = db.get_by_belonging('学术情报')

    for item in belonging_result:
        print(item['belonging_en'], item['sharing_url'])
