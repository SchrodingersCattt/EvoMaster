from fastapi import APIRouter

from src.apis import chat_api

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix='/chat/sessions')
