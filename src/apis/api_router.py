from fastapi import APIRouter

from src.apis import chat_api, debug_api

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix='/chat/sessions')
api_router.include_router(debug_api.router, prefix='/debug')
