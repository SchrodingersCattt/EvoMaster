"""用户级 wakeup SSE：任意 MatMaster 页面感知 session 后台唤醒。"""

from fastapi import APIRouter, Depends, Request

from src.apis.chat_api import _sse_streaming_response
from src.services.stream_service import ChatStreamService, get_stream_service
from src.services.user_service import UserService

router = APIRouter(tags=["Chat Wakeup"])


def _require_wakeup_user_id(request: Request) -> str:
    return UserService.get_user_id(request, required=True) or ""


@router.get(
    "/wakeup/stream",
    summary="用户级会话唤醒流",
    description="登录用户的 session 唤醒感知流：建连先发 snapshot，随后转发 live wakeup。",
    operation_id="streamUserWakeup",
)
async def wakeup_stream(
    request: Request,
    user_id: str = Depends(_require_wakeup_user_id),
    stream_svc: ChatStreamService = Depends(get_stream_service),
):
    return _sse_streaming_response(request, stream_svc.generate_wakeup_stream(user_id))
