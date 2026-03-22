"""MatMaster Local Web（本地调试，非生产平台 API）：FastAPI + WebSocket。

推荐在仓库根目录：``uv run python -m playground.mat_master.service.server``。
或在 ``playground/mat_master/service`` 下：``uv run uvicorn server:app --host 0.0.0.0``。
"""

from .app import app

__all__ = ['app']
