"""FastAPI application factory for the MatMaster web service."""

from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from . import bootstrap  # noqa: F401  — side effect: path + playground registration
from .http_routes import router as http_router
from .playground_init import lifespan
from .websocket_chat import websocket_chat_endpoint

app = FastAPI(title='MatMaster Web Service', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(http_router)


@app.websocket('/ws/chat')
async def _websocket_chat(websocket: WebSocket):
    await websocket_chat_endpoint(websocket)
