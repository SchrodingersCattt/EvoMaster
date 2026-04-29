"""飞书开放平台：事件回调（HTTP）、账号绑定、应用配置管理。"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.dao.feishu_app_config_table import FeishuAppConfig, get_feishu_app_config_table
from src.dao.feishu_binding_table import get_feishu_binding_table
from src.services.feishu_inbound_service import (
    handle_feishu_im_message_event_v1,
    process_feishu_event_request,
)
from src.services.user_service import UserService

logger = logging.getLogger(__name__)

router = APIRouter(tags=['Feishu'])


# ---------- 应用配置 ----------


class FeishuAppConfigBody(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    app_id: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)
    encrypt_key: str | None = None
    verify_token: str | None = None


@router.post(
    '/app-config',
    summary='创建或更新飞书应用配置',
    description='租户在网页端填写自己的飞书自建应用凭据。tenant_id 用于生成专属回调地址。',
)
def upsert_app_config(
    body: FeishuAppConfigBody,
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_app_config_table()
    cfg = FeishuAppConfig(
        tenant_id=body.tenant_id.strip(),
        app_id=body.app_id.strip(),
        app_secret=body.app_secret.strip(),
        encrypt_key=body.encrypt_key,
        verify_token=body.verify_token,
        created_by=user_id,
    )
    ok = table.upsert(cfg)
    if not ok:
        return JSONResponse(content={'code': 1, 'msg': 'save failed'}, status_code=500)
    return JSONResponse(content={'code': 0, 'msg': 'ok'})


@router.get(
    '/app-config/{tenant_id}',
    summary='查询飞书应用配置',
)
def get_app_config(
    tenant_id: str,
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_app_config_table()
    cfg = table.get_by_tenant_id(tenant_id)
    if not cfg:
        return JSONResponse(content={'code': 1, 'msg': 'not found'}, status_code=404)
    return JSONResponse(content={
        'code': 0,
        'data': {
            'tenant_id': cfg.tenant_id,
            'app_id': cfg.app_id,
            'encrypt_key': cfg.encrypt_key,
            'verify_token': cfg.verify_token,
        },
    })


@router.delete(
    '/app-config/{tenant_id}',
    summary='删除飞书应用配置',
)
def delete_app_config(
    tenant_id: str,
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_app_config_table()
    table.delete_by_tenant_id(tenant_id)
    return JSONResponse(content={'code': 0, 'msg': 'ok'})


# ---------- 事件回调 ----------


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


# ---------- 账号绑定 ----------


class FeishuBindBody(BaseModel):
    open_id: str = Field(..., min_length=1, description='飞书用户 open_id')
    tenant_id: str = Field(..., min_length=1, description='关联的 tenant_id')


@router.post(
    '/bind',
    summary='绑定飞书 open_id',
    description='需登录。将平台账号与飞书 open_id 关联（按 tenant 区分）。',
)
def feishu_bind(
    body: FeishuBindBody,
    user_id: Annotated[str, Depends(UserService.require_user_id)],
) -> JSONResponse:
    table = get_feishu_binding_table()
    ok = table.upsert_binding(body.open_id.strip(), user_id, tenant_id=body.tenant_id.strip())
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
    tenant_id: str | None = None,
) -> JSONResponse:
    table = get_feishu_binding_table()
    table.delete_for_user(user_id, tenant_id=tenant_id)
    return JSONResponse(content={'code': 0, 'msg': 'ok'})
