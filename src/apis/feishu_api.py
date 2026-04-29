"""飞书事件回调（仅保留 events 接口，配置与绑定在 matmaster-tools-server）。"""

import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from src.services.feishu_inbound_service import (
    handle_feishu_im_message_event_v1,
    process_feishu_event_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=['Feishu'])


@router.post(
    '/events/{tenant_id}',
    summary='飞书事件订阅回调',
    description=(
        '每个租户拥有独立的回调地址 /events/{tenant_id}，'
        '在飞书开放平台「事件与回调」中填写对应地址。'
    ),
)
async def feishu_event_callback(
    tenant_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    raw = await request.body()
    hdrs = {k: v for k, v in request.headers.items()}
    body, status, im_event, app_cfg = await process_feishu_event_request(
        tenant_id=tenant_id, raw_body=raw, headers=hdrs
    )
    if im_event is not None and app_cfg is not None:
        background_tasks.add_task(handle_feishu_im_message_event_v1, im_event, app_cfg)
    return JSONResponse(content=body, status_code=status)
