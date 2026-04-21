"""飞书开放平台：事件回调（HTTP）、账号绑定。"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.dao.feishu_binding_table import get_feishu_binding_table
from src.services.feishu_inbound_service import (
    handle_feishu_im_message_event_v1,
    process_feishu_event_request,
)
from src.services.user_service import UserService

logger = logging.getLogger(__name__)

router = APIRouter(tags=['Feishu'])


class FeishuBindBody(BaseModel):
    """将当前登录用户绑定到飞书 open_id（需在飞书侧能对应到同一用户，测试可手工填）"""

    open_id: str = Field(..., min_length=1, description='飞书用户 open_id')


@router.post(
    '/events',
    summary='飞书事件订阅回调',
    description=(
        '在飞书开放平台「事件与回调」中填写本地址（需公网 HTTPS），'
        '订阅 im.message.receive_v1。支持 Encrypt Key 与签名校验。'
    ),
)
async def feishu_event_callback(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    raw = await request.body()
    hdrs = {k: v for k, v in request.headers.items()}
    body, status, im_event = await process_feishu_event_request(
        raw_body=raw, headers=hdrs
    )
    if im_event is not None:
        background_tasks.add_task(handle_feishu_im_message_event_v1, im_event)
    return JSONResponse(content=body, status_code=status)


@router.post(
    '/bind',
    summary='绑定飞书 open_id',
    description='需登录（X-User-Id）。将平台账号与飞书 open_id 关联，飞书消息才能关联到配额与会话。',
)
def feishu_bind(
    body: FeishuBindBody,
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_binding_table()
    ok = table.upsert_binding(body.open_id.strip(), user_id)
    if not ok:
        return JSONResponse(
            content={'code': 1, 'msg': 'binding failed'},
            status_code=500,
        )
    return JSONResponse(content={'code': 0, 'msg': 'ok'})


@router.delete(
    '/bind',
    summary='解绑飞书',
)
def feishu_unbind(
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_binding_table()
    table.delete_for_user(user_id)
    return JSONResponse(content={'code': 0, 'msg': 'ok'})
