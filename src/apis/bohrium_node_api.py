"""Authenticated lifecycle operations for reusable Bohrium Nodes."""

from fastapi import APIRouter, Body, Depends

from src.base.base_res import BaseResponse
from src.models.chat import BohriumNodeStopRequest, ErrorApiResponse
from src.services.bohrium_node_lifecycle import (
    BohriumNodeLeaseManager,
    NodeIdentity,
    get_bohrium_node_lease_manager,
)
from src.services.bohrium_run_support import _creator_id_from_user
from src.services.user_service import UserService
from src.utils.exceptions import (
    BadRequestErrorResponse,
    BaseErrorResponse,
    ConflictErrorResponse,
)

router = APIRouter(tags=["Bohrium Node Runtime"])

_ERROR_RESPONSES = {
    400: {"model": ErrorApiResponse, "description": "请求参数不合法"},
    401: {"model": ErrorApiResponse, "description": "缺少或无效的 X-User-Id"},
    409: {"model": ErrorApiResponse, "description": "资源状态冲突"},
    503: {"model": ErrorApiResponse, "description": "服务暂不可用"},
}


@router.post(
    "/runtime/bohrium-node/stop",
    response_model=BaseResponse,
    summary="手动关闭当前用户的 Bohrium Node",
    description="仅在指定 user/org/project/SKU 槽位没有 live invocation lease 时 stop。",
    operation_id="stopCurrentUserBohriumNode",
    responses=_ERROR_RESPONSES,
)
def stop_bohrium_node(
    body: BohriumNodeStopRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    org_id: str | None = Depends(UserService.optional_org_id),
    manager: BohriumNodeLeaseManager = Depends(get_bohrium_node_lease_manager),
):
    if not org_id:
        raise BadRequestErrorResponse(msg="缺少组织标识 X-Org-Id")
    access_key = UserService.get_bohrium_access_key(user_id, org_id)
    if not access_key:
        raise BaseErrorResponse(
            http_status=503,
            code=503,
            msg="无法获取 Bohrium 凭证，请稍后重试",
        )
    identity = NodeIdentity(user_id, org_id, body.project_id, body.sku_id)
    try:
        stopped = manager.manual_stop(
            identity,
            access_key=access_key,
            creator_id=_creator_id_from_user(user_id),
        )
    except RuntimeError as exc:
        raise ConflictErrorResponse(
            msg="Node 正在使用或状态正在变化，暂时无法关机"
        ) from exc
    return BaseResponse(data={"stopped": stopped})
