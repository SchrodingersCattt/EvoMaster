import asyncio
import hmac
import json
import logging
from pathlib import Path as FsPath

from fastapi import APIRouter, Body, Depends, Header, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from matmaster.config.loader import load_llm_config
from matmaster.integration.event_payloads import build_public_sse_payload_from_bus_dump
from matmaster.types.events import InteractionReplyEvent
from src.apis.model_profile_errors import invalid_model_profile_error
from src.apis.sse_compression import gzip_sse_stream, should_gzip_sse
from src.base.base_res import BaseResponse
from src.dao.redis_dao import get_redis_dao
from src.models.chat import (
    BohriumSubmitConfirmationApiResponse,
    BohriumSubmitConfirmationData,
    BohriumSubmitConfirmationSetRequest,
    ChatSendRequest,
    ErrorApiResponse,
    InteractionReplyRequest,
    RunStatusApiResponse,
    RunStatusData,
    SessionDirectoryApiResponse,
    SessionDirectoryData,
    SessionDirectoryDeleteApiResponse,
    SessionDirectoryDeleteData,
    SessionDirectoryDeleteQuery,
    SessionDirectorySetRequest,
    SessionListApiResponse,
    SessionListMoreApiResponse,
    SessionListMoreQuery,
    SessionListMoreResponse,
    SessionListQuery,
    SessionListResponse,
    SessionTitleApiResponse,
    SessionTitleData,
    SessionTitlesApiResponse,
    SessionTitlesData,
    SessionTitleSetRequest,
    SessionTitlesQueryRequest,
    ShareSetRequest,
    ShareStatusApiResponse,
    ShareStatusData,
)
from src.services.agent_run_service import _get_agent_default_llm
from src.services.events_service import ChatEventsService, get_events_service
from src.services.image_input_service import ImageInputError, get_image_input_service
from src.services.llm_profile_validation import (
    InvalidModelProfileError,
    validate_platform_model_profile,
)
from src.services.quota_service import check_quota_status
from src.services.session_directory_service import (
    SessionDirectoryError,
    normalize_session_directory_for_storage,
)
from src.services.sessions_service import (
    DELETE_BLOCKED_STATUSES,
    ChatSessionsService,
    get_sessions_service,
)
from src.services.stream_service import (
    ChatStreamService,
    get_stream_service,
)
from src.services.stream_types import TriggerStreamContext
from src.services.user_service import UserService
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import INTERNAL_TRIGGER_TOKEN, REDIS_URL
from src.utils.exceptions import (
    BaseErrorResponse,
    ConflictErrorResponse,
    ForbiddenErrorResponse,
    NotFoundErrorResponse,
)

COMMON_ERROR_RESPONSES = {
    400: {"model": ErrorApiResponse, "description": "请求参数不合法"},
    401: {"model": ErrorApiResponse, "description": "缺少或无效的 X-User-Id"},
    403: {"model": ErrorApiResponse, "description": "无权限访问该资源"},
    404: {"model": ErrorApiResponse, "description": "资源不存在"},
    409: {"model": ErrorApiResponse, "description": "资源状态冲突"},
    503: {"model": ErrorApiResponse, "description": "服务暂不可用"},
}


router = APIRouter(tags=["Chat Sessions"])
_MAX_REPLY_PAYLOAD_BYTES = 256 * 1024

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_streaming_response(request: Request, generator) -> StreamingResponse:
    """构造 SSE StreamingResponse；客户端接受 gzip 时对事件流做流式压缩。

    历史回放体量大（数 MB JSON 文本），gzip 通常可压缩 4~6 倍，是当前加载耗时的主因。
    前端走浏览器透明解压，无需改动；可用 SSE_GZIP_ENABLED=0 快速回退。
    """
    headers = dict(SSE_HEADERS)
    content = generator
    if should_gzip_sse(request):
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
        content = gzip_sse_stream(generator)
    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers=headers,
    )


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_PROJECT_ROOT = FsPath(__file__).resolve().parent.parent.parent


def _session_workspace_data_from_row(row: dict) -> SessionDirectoryData:
    """从 evo_chat_sessions 行构造 session-directory 响应 data。"""
    raw = row.get("session_directory")
    if raw is None:
        directory: str | None = None
    else:
        s = str(raw).strip()
        directory = s if s else None
    raw_m = row.get("chat_mode")
    mode: str | None = None
    if raw_m is not None:
        m = str(raw_m).strip().lower()
        if m in ("direct", "planner"):
            mode = m
    return SessionDirectoryData(directory=directory, mode=mode)


def _session_bohrium_submit_confirmation_data_from_row(
    session_id: str,
    row: dict,
) -> BohriumSubmitConfirmationData:
    raw = row.get("bohrium_submit_confirmation_required")
    return BohriumSubmitConfirmationData(
        session_id=session_id,
        required=None if raw is None else bool(raw),
        source="session" if raw is not None else "default",
    )


def _session_directory_error(exc: SessionDirectoryError) -> BaseErrorResponse:
    return BaseErrorResponse(
        http_status=exc.http_status,
        code=exc.http_status,
        msg=exc.message,
        data={"error_code": exc.error_code},
    )


async def _handle_internal_trigger(
    request: Request,
    sid: str,
    req: ChatSendRequest | None,
    chat_svc: ChatSessionsService,
    stream_svc: ChatStreamService,
):
    """X-Internal-Token 通过后的内部发起：以 session owner 为计费/鉴权主体。"""
    prompt = (req.content or "").strip() if req else ""
    if not prompt:
        raise BaseErrorResponse(
            http_status=400, code=400, msg="内部触发需要非空 content"
        )
    session_row = chat_svc.get_session(sid) or {}
    owner = str(session_row.get("user_id") or "") or None
    if not owner:
        raise NotFoundErrorResponse(msg="会话不存在或无所有者，无法内部触发")
    # project 归属随行取：让平台一并判定「项目扣费能否兜底」（org_wallet_pass），
    # 否则没光子的项目付费用户会被闸口误拦。
    quota_status = await check_quota_status(
        owner, project_id=session_row.get("project_id")
    )
    if quota_status.is_exhausted:
        raise ForbiddenErrorResponse(
            msg=quota_status.exhausted_message("额度已用完，无法触发")
        )
    if not REDIS_URL:
        raise BaseErrorResponse(
            http_status=503, code=503, msg="队列服务不可用，请检查 REDIS_URL 配置"
        )
    # prepare 是同步函数（多次 DB/Redis 往返），放线程池避免卡事件循环。
    prep = await asyncio.to_thread(
        stream_svc.prepare_internal_trigger_run,
        sid,
        prompt,
        origin=(req.origin or "external_tool"),
        dedup_key=req.dedup_key,
        delivery=req.delivery,
        mode=req.mode,
        model=req.model,
    )
    if isinstance(prep, TriggerStreamContext):
        return _sse_streaming_response(
            request, stream_svc.generate_internal_trigger_stream(sid, prep)
        )
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "msg": prep.status,
            "data": {
                "status": prep.status,
                "task_id": prep.task_id,
                "invocation_id": prep.invocation_id,
                "reason": prep.reason,
            },
        },
    )


@router.get(
    "/list/more",
    response_model=SessionListMoreApiResponse,
    summary="会话列表单组加载更多",
    description="在 GET /list 某一目录组首屏之后，按该组返回的 `next_cursor` 继续拉取。"
    " 须指定 `project_id`、与首屏一致的目录（`directory` 或 `unset_directory=true`）、以及 `cursor`。",
    operation_id="listChatSessionsMore",
    responses={
        400: COMMON_ERROR_RESPONSES[400],
        401: COMMON_ERROR_RESPONSES[401],
    },
)
def list_sessions_more(
    query: SessionListMoreQuery = Depends(),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """单目录组内分页（游标）。"""
    directory = None if query.unset_directory else query.directory
    raw = chat_svc.list_sessions_more_in_directory(
        user_id=user_id,
        project_id=query.project_id,
        directory=directory,
        limit=query.limit,
        cursor_token=query.cursor,
    )
    return SessionListMoreApiResponse(
        data=SessionListMoreResponse.model_validate(raw),
    )


@router.get(
    "/list",
    response_model=SessionListApiResponse,
    summary="查询会话列表",
    description="按当前登录用户查询会话列表，必须传 `project_id`。"
    " 返回结果按 `session_directory` 分组；未设置目录的会话在 `session_directory=null` 组，且该组在最后。"
    " 每组首屏最多 `per_group_limit` 条（组内按更新时间倒序）；组内更多会话通过 GET /list/more 与 `next_cursor` 加载。",
    operation_id="listChatSessions",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
    },
)
def list_sessions(
    query: SessionListQuery = Depends(),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """按工作区目录聚合的会话列表（每组首屏分页）。"""
    raw = chat_svc.list_sessions_grouped_by_directory(
        user_id=user_id,
        project_id=query.project_id,
        per_group_limit=query.per_group_limit,
    )
    return SessionListApiResponse(
        data=SessionListResponse.model_validate(raw),
    )


@router.post(
    "/titles",
    response_model=SessionTitlesApiResponse,
    summary="批量查询会话标题",
    description="按 sessionId 批量获取当前登录用户自己的会话标题（含 first_user_message 供前端回退）。"
    " 仅返回归属当前用户且未删除的会话，其余 id 静默忽略；单次最多 200 个。"
    " 用于任务列表等场景按 sessionId 展示会话名称，避免逐条查询。",
    operation_id="getChatSessionTitles",
    responses={
        400: COMMON_ERROR_RESPONSES[400],
        401: COMMON_ERROR_RESPONSES[401],
    },
)
def get_session_titles(
    body: SessionTitlesQueryRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """按 sessionId 批量取标题（owner 范围，未命中静默忽略）。"""
    items = chat_svc.get_session_titles_by_ids(user_id, body.session_ids)
    return SessionTitlesApiResponse(data=SessionTitlesData(sessions=items))


@router.get(
    "/run_status",
    response_model=RunStatusApiResponse,
    summary="查询运行队列状态",
    description="返回当前系统中执行中的任务数和排队中的任务数，无需认证。",
    operation_id="getChatSessionRunStatus",
)
def get_run_status():
    """获取执行中与排队中的任务数（Redis session_run_owner + agent_run_queue），无需认证。"""
    registry = get_worker_registry_service()
    return RunStatusApiResponse(
        data=RunStatusData(
            active_count=registry.count_active_runs(),
            queued_count=registry.count_queued_runs(),
        ),
    )


@router.post(
    "/{session_id}/stream",
    summary="发送消息或订阅会话流",
    description="统一 SSE 流接口。"
    " `content` 为空或不传 body 时，仅订阅该会话的历史和心跳；"
    " `content` 非空时，发送消息并返回本次运行的 SSE 流。"
    " 可选 `directory`：随用户 query 写入历史事件；会话目录持久化请使用 PUT …/session-directory。"
    "\n\n目录相关错误码：directory_invalid_type, directory_invalid_chars, directory_must_be_absolute,"
    " directory_outside_roots, session_directory_invalid",
    operation_id="streamChatSession",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        403: COMMON_ERROR_RESPONSES[403],
        409: COMMON_ERROR_RESPONSES[409],
        503: COMMON_ERROR_RESPONSES[503],
    },
)
async def chat_stream(
    request: Request,
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    req: ChatSendRequest | None = Body(
        None,
        openapi_examples={
            "subscribe_only": {
                "summary": "仅订阅",
                "description": "不发送新消息，只建立 SSE 订阅。",
                "value": {"content": "", "mode": "direct"},
            },
            "send_message": {
                "summary": "发送消息",
                "description": "发送一条新消息，并返回本次运行的 SSE 流。",
                "value": {
                    "content": "请总结项目 42 下最近一次实验结果",
                    "mode": "direct",
                    "bohrium_project_id": 42,
                    "directory": "/share/workspace/run1",
                },
            },
        },
    ),
    user_id: str | None = Depends(UserService.optional_user_id),
    org_id: str | None = Depends(UserService.optional_org_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
):
    """ag-ui：统一流接口。会话已分享时可不鉴权；未分享时需登录且为会话所有者。

    第二轮无推送排查：后端日志看是否有 stream 409（会话占用）、generate_send_stream: start（已开流）、
    run_agent 报错；前端需消费本次 POST 的 response body（SSE）并合并到 UI，不能只依赖「订阅」连接。"""
    sid = session_id.strip()
    is_share_route = request.url.path.startswith("/pubapi/")
    internal_token = x_internal_token if isinstance(x_internal_token, str) else None
    if internal_token:
        if not INTERNAL_TRIGGER_TOKEN or not hmac.compare_digest(
            internal_token, INTERNAL_TRIGGER_TOKEN
        ):
            raise ForbiddenErrorResponse(msg="内部触发 token 无效")
        if is_share_route:
            raise ForbiddenErrorResponse(msg="分享页仅支持只读订阅，不允许内部触发")
        return await _handle_internal_trigger(request, sid, req, chat_svc, stream_svc)

    has_content = req is not None and bool((req.content or "").strip())
    if is_share_route and has_content:
        raise ForbiddenErrorResponse(msg="分享页仅支持只读订阅，不允许发送消息")
    logger.info(
        "stream request: session_id=%s user_id=%s has_content=%s share_route=%s",
        sid,
        user_id,
        has_content,
        is_share_route,
    )
    allow_admin_read = not has_content
    if not chat_svc.can_access_session(sid, user_id, allow_admin_read=allow_admin_read):
        logger.warning(
            "stream 403: can_access_session denied session_id=%s user_id=%s",
            sid,
            user_id,
        )
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    chat_svc.ensure_session(sid, user_id=user_id)
    user_prompt = (req.content or "").strip() if req else ""
    subscribe_only = not user_prompt

    if subscribe_only:
        return _sse_streaming_response(
            request, stream_svc.generate_subscribe_stream(sid)
        )

    # 发送消息前检查额度（计价化：金额额度 <= 0 则 403；模型级限制已并入金额额度）
    assert req is not None
    if user_id:
        # project 归属：req.bohrium_project_id 优先（本次发送会把会话归属写成它，
        # 计费扣的就是它，见 stream_service 的 set_session_bohrium），
        # 没带则回落会话行既有归属。读行失败按 None（闸口退化为 免费额度/光子）。
        gate_project_id = req.bohrium_project_id
        if gate_project_id in (None, ""):
            try:
                session_row = chat_svc.get_session(sid) or {}
                gate_project_id = session_row.get("project_id")
            except Exception:  # noqa: BLE001 - 拿不到归属不阻断闸口检查
                gate_project_id = None
        quota_status = await check_quota_status(user_id, project_id=gate_project_id)
        remaining = quota_status.remaining_yuan
        logger.info(
            "stream quota check: session_id=%s user_id=%s remaining=%s reset_at=%s",
            sid,
            user_id,
            remaining,
            quota_status.reset_at,
        )
        if quota_status.is_exhausted:
            # 403 时打出请求详情便于 UAT 排查
            req_headers = dict(request.headers) if request else {}
            safe_headers = {}
            for k, v in req_headers.items():
                k_lower = k.lower()
                if k_lower == "authorization":
                    safe_headers[k] = "(present)" if v else "(absent)"
                elif k_lower in ("x-user-id", "x-org-id", "content-type"):
                    safe_headers[k] = v[:128] + "..." if len(v) > 128 else v
            body_summary = {
                "content_len": len(req.content or ""),
                "files_count": len(req.files) if req.files else 0,
                "has_bohrium_user_id": getattr(req, "bohrium_user_id", None)
                is not None,
            }
            logger.warning(
                "stream 403: quota exhausted session_id=%s user_id=%s remaining=%s "
                "path=%s method=%s headers=%s body_summary=%s",
                sid,
                user_id,
                remaining,
                request.url.path if request else None,
                request.method if request else None,
                safe_headers,
                body_summary,
            )
            raise ForbiddenErrorResponse(
                msg=quota_status.exhausted_message("免费额度已用完，请稍后再试。"),
            )

    # 仅 Worker 队列模式：发送消息需 REDIS_URL，否则返回 503
    if not REDIS_URL:
        logger.warning(
            "stream: REDIS_URL not configured, send unavailable session_id=%s", sid
        )
        raise BaseErrorResponse(
            http_status=503,
            code=503,
            msg="队列服务不可用，请检查 REDIS_URL 配置",
        )
    # 发送消息并返回本次运行的 SSE 流（此时 req 必存在且 content 非空）；org_id 从上游 Header X-Org-Id 获取
    logger.info(
        "stream prepare: session_id=%s user_id=%s has_org_id=%s",
        sid,
        user_id,
        bool(org_id),
    )
    if not (req.byok_credential_id or "").strip():
        try:
            validate_platform_model_profile(req.model)
        except InvalidModelProfileError as exc:
            raise invalid_model_profile_error(exc) from exc
    if req.images:
        image_service = get_image_input_service()
        try:
            validated_images = image_service.validate_current_images(
                files=req.files or [],
                images=req.images,
            )
            llm_config = load_llm_config(_PROJECT_ROOT / "config" / "llm_config.yaml")
            image_service.ensure_vision_supported(
                llm_config=llm_config,
                model_override=(req.model or "").strip() or None,
                default_profile_key=_get_agent_default_llm(),
            )
        except ImageInputError as exc:
            raise BaseErrorResponse(
                http_status=exc.http_status,
                code=exc.http_status,
                msg=exc.message,
                data={"error_code": exc.error_code},
            ) from exc
        req = req.model_copy(
            update={"images": [image.url for image in validated_images]}
        )
    try:
        ctx = stream_svc.prepare_send_message(sid, req, user_id, org_id=org_id)
    except InvalidModelProfileError as exc:
        raise invalid_model_profile_error(exc) from exc
    except SessionDirectoryError as exc:
        raise _session_directory_error(exc) from exc
    if ctx is None:
        logger.warning(
            "stream 409: session already running session_id=%s (wait for current run or call stop)",
            sid,
        )
        raise ConflictErrorResponse(
            msg="该会话已有任务在运行，请等待完成或先取消后再发新消息",
        )
    return _sse_streaming_response(request, stream_svc.generate_send_stream(sid, ctx))


@router.post(
    "/{session_id}/stop",
    response_model=BaseResponse,
    summary="停止会话运行",
    description="终止该会话当前正在运行的任务。若会话正在等待用户交互回复，也会同时唤醒并取消阻塞线程。",
    operation_id="stopChatSession",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def stop_session(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """终止该会话当前正在运行的任务。有权限访问即可调用；多 worker 时通过 Redis 广播，始终返回 200。
    若当前正在等待用户交互回复，会向 per-request reply key 投递取消哨兵以立即唤醒阻塞线程。"""
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    dao = get_redis_dao()
    active_request_id = dao.get_active_interaction(sid)
    if active_request_id:
        dao.finalize_interaction(active_request_id, "cancelled")
        dao.rpush_interaction_cancel(active_request_id)
    chat_svc.stop_session_run(sid)
    return BaseResponse(msg="ok")


@router.post(
    "/{session_id}/interactions/{request_id}/reply",
    response_model=BaseResponse,
    summary="提交交互回复",
    description="对 interaction_request 提交回复，Agent 会继续执行。",
    operation_id="replyChatSessionInteraction",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
        404: COMMON_ERROR_RESPONSES[404],
        409: COMMON_ERROR_RESPONSES[409],
    },
)
async def interaction_reply(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    request_id: str = Path(..., description="交互请求 ID", examples=["aq_xxx"]),
    req: InteractionReplyRequest = Body(...),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
    events_svc: ChatEventsService = Depends(get_events_service),
):
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    dao = get_redis_dao()
    record = dao.read_pending_interaction(request_id)
    if record is None:
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if record.get("session_id") != sid:
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if record.get("kind") != req.kind:
        raise ConflictErrorResponse(msg="交互类型不匹配")
    if (
        len(json.dumps(req.payload, ensure_ascii=False).encode())
        > _MAX_REPLY_PAYLOAD_BYTES
    ):
        raise ConflictErrorResponse(msg="回复内容过大")

    envelope = json.dumps(
        {"kind": req.kind, "request_id": request_id, "payload": req.payload},
        ensure_ascii=False,
    )
    result = dao.answer_pending_interaction(request_id, envelope)
    if result == "not_found":
        raise NotFoundErrorResponse(msg="交互请求不存在或已过期")
    if result == "not_pending":
        raise ConflictErrorResponse(msg="交互已 answered/timeout/cancelled")

    if (
        req.kind == "submit_review"
        and req.payload.get("decision") == "submit"
        and req.payload.get("disable_future_confirmation") is True
        and not chat_svc.set_bohrium_submit_confirmation(sid, user_id, False)
    ):
        logger.warning("disable future submit confirmation failed: session_id=%s", sid)

    reply = InteractionReplyEvent(
        source="User",
        kind=req.kind,
        request_id=request_id,
        payload=req.payload,
    )
    public = build_public_sse_payload_from_bus_dump(
        reply.model_dump(mode="json"),
        session_id=sid,
        task_id=record.get("task_id"),
        invocation_id=record.get("invocation_id"),
        spawn_id=None,
    )
    stream_svc.publish_reply_event(sid, public)
    events_svc.add_history_event(sid, public, user_id=user_id)
    return BaseResponse(msg="ok")


@router.get(
    "/{session_id}/share",
    response_model=ShareStatusApiResponse,
    summary="查询会话分享状态",
    description="查看会话是否已开启分享。已分享时任何人可查看；未分享时需为会话所有者。",
    operation_id="getChatSessionShareStatus",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def get_share_status(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """查看分享状态。已分享时会话任何人可查看；未分享时需为会话所有者。"""
    status = chat_svc.get_share_status(session_id)
    if not status["enabled"] and not chat_svc.can_access_session(
        session_id, user_id, allow_admin_read=True
    ):
        raise ForbiddenErrorResponse(msg="无权限查看该会话")
    return ShareStatusApiResponse(
        data=ShareStatusData(enabled=status["enabled"]),
    )


@router.put(
    "/{session_id}/share",
    response_model=ShareStatusApiResponse,
    summary="设置会话分享状态",
    description="仅会话所有者可设置。开启分享后，其他人可在未登录场景下访问该会话的分享能力。",
    operation_id="setChatSessionShareStatus",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def set_share_status(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    body: ShareSetRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """设置分享状态。仅会话所有者可设置。开启后，stream 等接口可不鉴权访问。"""
    if not chat_svc.set_share_status(session_id, enabled=body.enabled, user_id=user_id):
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
    return ShareStatusApiResponse(
        data=ShareStatusData(enabled=body.enabled),
    )


@router.get(
    "/{session_id}/session-directory",
    response_model=SessionDirectoryApiResponse,
    summary="查询会话工作区目录与模式偏好",
    description="返回该会话绑定的工作区目录（如 Bohrium 远端路径）及偏好模式 direct|planner。"
    " 已分享会话任何人可读；未分享时需为所有者或 admin 只读白名单。",
    operation_id="getChatSessionDirectory",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def get_session_directory(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """查询会话工作区目录与模式偏好。"""
    sid = session_id.strip()
    row = chat_svc.get_session(sid)
    if not row:
        raise NotFoundErrorResponse(msg="Session not found")
    if not chat_svc.can_access_session(sid, user_id, allow_admin_read=True):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    return SessionDirectoryApiResponse(data=_session_workspace_data_from_row(row))


@router.put(
    "/{session_id}/session-directory",
    response_model=SessionDirectoryApiResponse,
    summary="设置会话工作区目录与模式偏好",
    description="仅会话所有者可写；可只更新 directory、只更新 mode，或两者同时更新；"
    " 未出现在请求体中的字段保持不变。发送消息时也会自动持久化本轮 mode。"
    "\n\n目录相关错误码：directory_invalid_type, directory_invalid_chars, directory_must_be_absolute,"
    " directory_outside_roots, session_directory_invalid",
    operation_id="setChatSessionDirectory",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def set_session_directory(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    body: SessionDirectorySetRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """设置或清除会话工作区目录与/或模式偏好。"""
    sid = session_id.strip()
    updates = body.model_dump(exclude_unset=True)
    kwargs: dict = {}
    normalized_directory: str | None = None
    if "directory" in updates:
        try:
            normalized_directory = normalize_session_directory_for_storage(
                body.directory
            )
        except SessionDirectoryError as exc:
            raise _session_directory_error(exc) from exc
        kwargs["directory"] = normalized_directory
    if "mode" in updates:
        kwargs["chat_mode"] = body.mode

    if "directory" in updates and "mode" not in updates:
        if not chat_svc.set_session_directory(sid, normalized_directory, user_id):
            raise NotFoundErrorResponse(
                msg="Session not found or you are not the owner",
            )
    elif kwargs:
        if not chat_svc.update_session_workspace_prefs(sid, user_id, **kwargs):
            raise NotFoundErrorResponse(
                msg="Session not found or you are not the owner",
            )
    else:
        row = chat_svc.get_session(sid)
        if not row or row.get("user_id") != user_id:
            raise NotFoundErrorResponse(
                msg="Session not found or you are not the owner",
            )
        return SessionDirectoryApiResponse(data=_session_workspace_data_from_row(row))

    row = chat_svc.get_session(sid)
    if not row:
        raise NotFoundErrorResponse(msg="Session not found or you are not the owner")
    return SessionDirectoryApiResponse(data=_session_workspace_data_from_row(row))


@router.get(
    "/{session_id}/bohrium-submit-confirmation",
    response_model=BohriumSubmitConfirmationApiResponse,
    summary="查询会话级 Bohrium 任务提交确认偏好",
    description="仅返回会话级覆盖值；required 为 null 表示当前会话未设置，是否继承/默认由后续消费链路处理。",
    operation_id="getChatSessionBohriumSubmitConfirmation",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def get_bohrium_submit_confirmation(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    sid = session_id.strip()
    row = chat_svc.get_session(sid)
    if not row:
        raise NotFoundErrorResponse(msg="Session not found")
    if not chat_svc.can_access_session(sid, user_id, allow_admin_read=True):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    return BohriumSubmitConfirmationApiResponse(
        data=_session_bohrium_submit_confirmation_data_from_row(sid, row)
    )


@router.put(
    "/{session_id}/bohrium-submit-confirmation",
    response_model=BohriumSubmitConfirmationApiResponse,
    summary="设置会话级 Bohrium 任务提交确认偏好",
    description="仅会话所有者可写；required=true/false 设置会话级覆盖，required=null 清除覆盖。",
    operation_id="setChatSessionBohriumSubmitConfirmation",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def set_bohrium_submit_confirmation(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    body: BohriumSubmitConfirmationSetRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    sid = session_id.strip()
    if not chat_svc.set_bohrium_submit_confirmation(sid, user_id, body.required):
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
    row = chat_svc.get_session(sid)
    if not row:
        raise NotFoundErrorResponse(msg="Session not found or you are not the owner")
    return BohriumSubmitConfirmationApiResponse(
        data=_session_bohrium_submit_confirmation_data_from_row(sid, row)
    )


@router.put(
    "/{session_id}/title",
    response_model=SessionTitleApiResponse,
    summary="重命名会话（设置/清除标题）",
    description="仅会话所有者可写。传非空 `title` 设置自定义标题；传 null 或空字符串清除，"
    "清除后前端回退到 first_user_message。标题最长 255 字符。",
    operation_id="setChatSessionTitle",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def set_session_title(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    body: SessionTitleSetRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """设置或清除会话自定义标题。仅会话所有者可写。"""
    sid = session_id.strip()
    if not chat_svc.set_session_title(sid, body.title, user_id):
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
    return SessionTitleApiResponse(data=SessionTitleData(id=sid, title=body.title))


@router.put(
    "/{session_id}/interrupt-hint",
    response_model=BaseResponse,
    summary="设置排队中断提示",
    description="前端有排队消息时调用，通知后端在下一个 checkpoint 时暂停等待确认。",
    operation_id="setChatSessionInterruptHint",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def set_interrupt_hint(
    session_id: str = Path(..., description="会话 ID"),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    get_redis_dao().set_interrupt_hint(sid)
    return BaseResponse(msg="ok")


@router.delete(
    "/{session_id}/interrupt-hint",
    response_model=BaseResponse,
    summary="清除排队中断提示",
    description="前端队列清空时调用，取消中断意图。",
    operation_id="deleteChatSessionInterruptHint",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def delete_interrupt_hint(
    session_id: str = Path(..., description="会话 ID"),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    get_redis_dao().delete_interrupt_hint(sid)
    return BaseResponse(msg="ok")


@router.post(
    "/{session_id}/interrupt",
    response_model=BaseResponse,
    summary="确认中断当前轮次",
    description="前端收到 checkpoint 事件后调用，确认要中断当前 tool dispatch。幂等：重复调用安全。",
    operation_id="confirmChatSessionInterrupt",
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def confirm_interrupt(
    session_id: str = Path(..., description="会话 ID"),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg="无权限访问该会话")
    get_redis_dao().set_interrupt_confirm(sid)
    return BaseResponse(msg="ok")


@router.delete(
    "/by-directory",
    response_model=SessionDirectoryDeleteApiResponse,
    summary="按会话目录整组删除会话",
    description="仅删除当前用户在指定项目、指定 session_directory 组下的会话；"
    "若组内存在 active/waiting 会话，则整组拒绝删除且不会部分删除。",
    operation_id="deleteChatSessionsByDirectory",
    responses={
        400: COMMON_ERROR_RESPONSES[400],
        401: COMMON_ERROR_RESPONSES[401],
        409: COMMON_ERROR_RESPONSES[409],
    },
)
def delete_sessions_by_directory(
    query: SessionDirectoryDeleteQuery = Depends(),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """整组软删除某个会话目录下的会话；运行中/排队中会话会阻断整组删除。"""
    directory: str | None = None
    if not query.unset_directory:
        try:
            directory = normalize_session_directory_for_storage(query.directory)
        except SessionDirectoryError as exc:
            raise _session_directory_error(exc) from exc
        if directory is None:
            raise BaseErrorResponse(
                http_status=400,
                code=400,
                msg="请指定 unset_directory=true 或非空 directory",
            )

    result = chat_svc.delete_sessions_by_directory(
        user_id=user_id,
        project_id=query.project_id,
        directory=directory,
    )
    if result.get("blocked_count", 0) > 0:
        raise ConflictErrorResponse(
            msg="该目录下存在运行中或排队中的会话，无法删除",
            data={
                "blocked_count": result.get("blocked_count", 0),
                "blocked_statuses": result.get("blocked_statuses", []),
            },
        )
    return SessionDirectoryDeleteApiResponse(
        msg="ok",
        data=SessionDirectoryDeleteData(
            deleted_count=int(result.get("deleted_count", 0)),
        ),
    )


@router.delete(
    "/{session_id}",
    response_model=BaseResponse,
    summary="删除会话",
    description="仅会话所有者可删除；删除为软删除，用户侧不再展示，关联聊天事件保留。",
    operation_id="deleteChatSession",
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def delete_session(
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """软删除会话。仅会话所有者可删除；运行中/排队中的会话不可删除。"""
    sid = session_id.strip()
    row = chat_svc.get_session(sid)
    if not row or row.get("user_id") != user_id:
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
    status = chat_svc.reconcile_waiting_status(sid, row.get("status"))
    if status in DELETE_BLOCKED_STATUSES:
        raise ConflictErrorResponse(
            msg="运行中或排队中的会话无法删除",
            data={"blocked_status": status},
        )
    if not chat_svc.delete_session(sid, user_id=user_id):
        raise NotFoundErrorResponse(
            msg="Session not found or you are not the owner",
        )
    return BaseResponse(msg="ok")
