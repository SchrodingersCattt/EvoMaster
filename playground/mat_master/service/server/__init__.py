"""MatMaster Web Service package (FastAPI + WebSocket).

Run with ``uvicorn server:app`` from ``playground/mat_master/service`` or
``python -m playground.mat_master.service.server``.
"""

from .app import app

__all__ = ['app']
