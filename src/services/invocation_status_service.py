from src.dao.invocation_status_db import InvocationStatusTable


class InvocationStatusService:
    def __init__(self, db: InvocationStatusTable = None):
        self.db = db or InvocationStatusTable()

    def update_status(
        self,
        invocation_id: str,
        status_code: int,
    ) -> dict:
        """
        更新 invocation 状态
        直接存储 status_code (int): 0 或 1
        """
        is_success = self.db.update(invocation_id, status_code)
        if not is_success:
            return {'code': -1, 'data': None, 'msg': 'error'}

        return {'code': 0, 'data': None, 'msg': 'success'}

    def get_status(
        self,
        invocation_id: str,
    ) -> dict:
        """
        获取 invocation 状态
        直接返回 status_code (int): 0 或 1
        """
        status_code = self.db.check(invocation_id)
        return {
            'code': 0,
            'data': {
                'invocation_id': invocation_id,
                'status_code': status_code,
            },
            'msg': 'success',
        }
