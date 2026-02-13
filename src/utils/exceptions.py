class BaseErrorResponse(Exception):
    def __init__(
        self, *, http_status: int = 400, code: int = 400, msg: str = 'error', data=None
    ):
        self.http_status = http_status
        self.code = code
        self.msg = msg
        self.data = data


class BadRequestErrorResponse(BaseErrorResponse):
    def __init__(self, *, code: int = 400, msg: str = 'Bad Request', data=None):
        super().__init__(http_status=400, code=code, msg=msg, data=data)


class ForbiddenErrorResponse(BaseErrorResponse):
    def __init__(self, *, code: int = 403, msg: str = '无权限访问', data=None):
        super().__init__(http_status=403, code=code, msg=msg, data=data)


class NotFoundErrorResponse(BaseErrorResponse):
    def __init__(self, *, code: int = 404, msg: str = 'Not Found', data=None):
        super().__init__(http_status=404, code=code, msg=msg, data=data)


class ConflictErrorResponse(BaseErrorResponse):
    def __init__(self, *, code: int = 409, msg: str = 'Conflict', data=None):
        super().__init__(http_status=409, code=code, msg=msg, data=data)
