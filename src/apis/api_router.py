from fastapi import APIRouter

from src.apis import (
    admin_api,
    chat_api,
    debug_api,
    feishu_api,
    wakeup_api,
)

api_router = APIRouter()
api_router.include_router(chat_api.router, prefix="/chat/sessions")
api_router.include_router(wakeup_api.router, prefix="/chat")
api_router.include_router(admin_api.router, prefix="/admin")
api_router.include_router(debug_api.router, prefix="/debug")
api_router.include_router(feishu_api.router, prefix="/integrations/feishu")
