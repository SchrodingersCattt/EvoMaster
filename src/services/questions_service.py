from src.dao.recommend_questions_db import RecommendQuestionsTable


class RecommendQuestionsService:
    def __init__(self, db: RecommendQuestionsTable):
        self.db = db

    def get_all_belonging(self):
        if result := self.db.get_all_belonging():
            return {'code': 0, 'data': result, 'msg': 'success'}
        else:
            return {'code': -1, 'data': [], 'msg': 'error'}

    def get_all(self):
        if result := self.db.get_all():
            return {'code': 0, 'data': result, 'msg': 'success'}
        else:
            return {'code': -1, 'data': {}, 'msg': 'error'}

    def get_by_belonging(self, belonging: str):
        result = self.db.get_by_belonging(belonging)
        if result is not None:
            return {'code': 0, 'data': result, 'msg': 'success'}
        else:
            return {'code': -1, 'data': {}, 'msg': 'error'}
