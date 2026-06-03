from fastapi import APIRouter

from src.apis import admin_chat_api, byok_api, chat_api, debug_api, feishu_api

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix='/chat/sessions')
api_router.include_router(admin_chat_api.router, prefix='/admin/chat/sessions')
api_router.include_router(debug_api.router, prefix='/debug')
api_router.include_router(feishu_api.router, prefix='/integrations/feishu')
api_router.include_router(byok_api.router, prefix='/llm-configs')
