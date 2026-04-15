"""分享态路由：仅暴露分享页需要的只读接口，不暴露 delete/stop/list/set_share 等写操作。

网关对 /share/* 路径免鉴权，请求不携带 X-User-Id；
后端通过 can_access_session 校验 is_shared 决定是否放行。
"""

from fastapi import APIRouter

from src.apis.chat_api import chat_stream

share_router = APIRouter()

share_chat_router = APIRouter(tags=['Share (public)'])
share_chat_router.add_api_route(
    '/{session_id}/stream',
    chat_stream,
    methods=['POST'],
    summary='[分享页] 订阅会话流（只读）',
    operation_id='shareStreamChatSession',
)

share_router.include_router(share_chat_router, prefix='/chat/sessions')
