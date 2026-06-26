from fastapi import APIRouter

from src.apis import admin_chat_api

router = APIRouter()
router.include_router(admin_chat_api.router, prefix="/chat/sessions")
router.include_router(admin_chat_api.user_router, prefix="/users")
