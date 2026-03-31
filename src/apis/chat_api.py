import logging

from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi.responses import StreamingResponse

from src.base.base_res import BaseResponse
from src.models.chat import (
    ChatPlannerReplyRequest,
    ChatSendRequest,
    ErrorApiResponse,
    RunStatusApiResponse,
    RunStatusData,
    SessionItem,
    SessionListApiResponse,
    SessionListQuery,
    SessionListResponse,
    ShareSetRequest,
    ShareStatusApiResponse,
    ShareStatusData,
)
from src.services.events_service import ChatEventsService, get_events_service
from src.services.quota_service import check_quota
from src.services.sessions_service import ChatSessionsService, get_sessions_service
from src.services.stream_service import (
    ChatStreamService,
    get_stream_service,
)
from src.services.user_service import UserService
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import REDIS_URL
from src.utils.exceptions import (
    BaseErrorResponse,
    ConflictErrorResponse,
    ForbiddenErrorResponse,
    NotFoundErrorResponse,
)

COMMON_ERROR_RESPONSES = {
    401: {'model': ErrorApiResponse, 'description': '缺少或无效的 X-User-Id'},
    403: {'model': ErrorApiResponse, 'description': '无权限访问该资源'},
    404: {'model': ErrorApiResponse, 'description': '资源不存在'},
    409: {'model': ErrorApiResponse, 'description': '资源状态冲突'},
    503: {'model': ErrorApiResponse, 'description': '服务暂不可用'},
}


router = APIRouter(tags=['Chat Sessions'])

SSE_HEADERS = {
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
}

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@router.get(
    '/list',
    response_model=SessionListApiResponse,
    summary='查询会话列表',
    description='按当前登录用户查询会话列表，支持分页和按 `project_id` 过滤。'
    ' 当传入 `project_id` 时，仅返回该项目下的会话。',
    operation_id='listChatSessions',
    responses={
        401: COMMON_ERROR_RESPONSES[401],
    },
)
def list_sessions(
    query: SessionListQuery = Depends(),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """会话列表，分页：首屏传 limit=20&offset=0，「加载更多」时增大 offset。"""
    sessions, total = chat_svc.list_sessions(
        user_id=user_id,
        limit=query.limit,
        offset=query.offset,
        project_id=query.project_id,
    )
    has_more = query.offset + len(sessions) < total
    return SessionListApiResponse(
        data=SessionListResponse(
            sessions=[SessionItem.model_validate(s) for s in sessions],
            total=total,
            has_more=has_more,
        ),
    )


@router.get(
    '/run_status',
    response_model=RunStatusApiResponse,
    summary='查询运行队列状态',
    description='返回当前系统中执行中的任务数和排队中的任务数，无需认证。',
    operation_id='getChatSessionRunStatus',
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
    '/{session_id}/stream',
    summary='发送消息或订阅会话流',
    description='统一 SSE 流接口。'
    ' `content` 为空或不传 body 时，仅订阅该会话的历史和心跳；'
    ' `content` 非空时，发送消息并返回本次运行的 SSE 流。',
    operation_id='streamChatSession',
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        403: COMMON_ERROR_RESPONSES[403],
        409: COMMON_ERROR_RESPONSES[409],
        503: COMMON_ERROR_RESPONSES[503],
    },
)
async def chat_stream(
    request: Request,
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    req: ChatSendRequest | None = Body(
        None,
        openapi_examples={
            'subscribe_only': {
                'summary': '仅订阅',
                'description': '不发送新消息，只建立 SSE 订阅。',
                'value': {'content': '', 'mode': 'direct'},
            },
            'send_message': {
                'summary': '发送消息',
                'description': '发送一条新消息，并返回本次运行的 SSE 流。',
                'value': {
                    'content': '请总结项目 42 下最近一次实验结果',
                    'mode': 'direct',
                    'bohrium_project_id': 42,
                },
            },
        },
    ),
    user_id: str | None = Depends(UserService.optional_user_id),
    org_id: str | None = Depends(UserService.optional_org_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
):
    """ag-ui：统一流接口。会话已分享时可不鉴权；未分享时需登录且为会话所有者。

    第二轮无推送排查：后端日志看是否有 stream 409（会话占用）、generate_send_stream: start（已开流）、
    run_agent 报错；前端需消费本次 POST 的 response body（SSE）并合并到 UI，不能只依赖「订阅」连接。"""
    sid = session_id.strip()
    has_content = req is not None and bool((req.content or '').strip())
    logger.info(
        'stream request: session_id=%s user_id=%s has_content=%s',
        sid,
        user_id,
        has_content,
    )
    if not chat_svc.can_access_session(sid, user_id):
        logger.warning(
            'stream 403: can_access_session denied session_id=%s user_id=%s',
            sid,
            user_id,
        )
        raise ForbiddenErrorResponse(msg='无权限访问该会话')
    chat_svc.ensure_session(sid, user_id=user_id)
    user_prompt = (req.content or '').strip() if req else ''
    subscribe_only = not user_prompt

    if subscribe_only:
        return StreamingResponse(
            stream_svc.generate_subscribe_stream(sid),
            media_type='text/event-stream',
            headers=SSE_HEADERS,
        )

    # 发送消息前检查配额（与 MatMaster 一致：有 user_id 时检查，无剩余则 403）
    assert req is not None
    if user_id:
        remaining = await check_quota(user_id)
        logger.info(
            'stream quota check: session_id=%s user_id=%s remaining=%s',
            sid,
            user_id,
            remaining,
        )
        if remaining <= 0:
            # 403 时打出请求详情便于 UAT 排查
            req_headers = dict(request.headers) if request else {}
            safe_headers = {}
            for k, v in req_headers.items():
                k_lower = k.lower()
                if k_lower == 'authorization':
                    safe_headers[k] = '(present)' if v else '(absent)'
                elif k_lower in ('x-user-id', 'x-org-id', 'content-type'):
                    safe_headers[k] = v[:128] + '...' if len(v) > 128 else v
            body_summary = {
                'content_len': len(req.content or ''),
                'files_count': len(req.files) if req.files else 0,
                'has_bohrium_user_id': getattr(req, 'bohrium_user_id', None)
                is not None,
            }
            logger.warning(
                'stream 403: quota exhausted session_id=%s user_id=%s remaining=%s '
                'path=%s method=%s headers=%s body_summary=%s',
                sid,
                user_id,
                remaining,
                request.url.path if request else None,
                request.method if request else None,
                safe_headers,
                body_summary,
            )
            raise ForbiddenErrorResponse(
                msg='当日免费额度已用完。请填写问卷申请额度，审核通过后再试。',
            )

    # 仅 Worker 队列模式：发送消息需 REDIS_URL，否则返回 503
    if not REDIS_URL:
        logger.warning(
            'stream: REDIS_URL not configured, send unavailable session_id=%s', sid
        )
        raise BaseErrorResponse(
            http_status=503,
            code=503,
            msg='队列服务不可用，请检查 REDIS_URL 配置',
        )
    # 发送消息并返回本次运行的 SSE 流（此时 req 必存在且 content 非空）；org_id 从上游 Header X-Org-Id 获取
    logger.info(
        'stream prepare: session_id=%s user_id=%s has_org_id=%s',
        sid,
        user_id,
        bool(org_id),
    )
    ctx = stream_svc.prepare_send_message(sid, req, user_id, org_id=org_id)
    if ctx is None:
        logger.warning(
            'stream 409: session already running session_id=%s (wait for current run or call stop)',
            sid,
        )
        raise ConflictErrorResponse(
            msg='该会话已有任务在运行，请等待完成或先取消后再发新消息',
        )
    # 给 agent 的 prompt：正文 + 附件 URL + 工作区路径；多轮历史由 run_agent 通过 task.meta['dialog_history'] 注入
    base_prompt = (req.content or '').strip()
    if req.files:
        base_prompt += '\n\n[Attached files]\n' + '\n'.join(req.files)
    if req.workspace_paths:
        base_prompt += '\n\n[Workspace paths]\n' + '\n'.join(req.workspace_paths)
    return StreamingResponse(
        stream_svc.generate_send_stream(sid, base_prompt, ctx),
        media_type='text/event-stream',
        headers=SSE_HEADERS,
    )


@router.post(
    '/{session_id}/stop',
    response_model=BaseResponse,
    summary='停止会话运行',
    description='终止该会话当前正在运行的任务。若会话正在等待用户确认，也会同时唤醒并取消阻塞线程。',
    operation_id='stopChatSession',
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def stop_session(
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
):
    """终止该会话当前正在运行的任务。有权限访问即可调用；多 worker 时通过 Redis 广播，始终返回 200。
    若当前正在等待用户确认（confirmation_request），会向回复队列投递取消哨兵以立即唤醒阻塞线程。"""
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg='无权限访问该会话')
    reply_queue = stream_svc.get_reply_queue(sid)
    if reply_queue is not None:
        try:
            reply_queue.put_cancel()
        except Exception:
            pass
    chat_svc.stop_session_run(sid)
    return BaseResponse(msg='ok')


@router.post(
    '/{session_id}/confirmation_reply',
    response_model=BaseResponse,
    summary='提交确认回复',
    description='当会话流返回 `confirmation_request` 时，调用本接口提交用户回复，Agent 会继续执行。',
    operation_id='replyChatSessionConfirmation',
    responses={
        403: COMMON_ERROR_RESPONSES[403],
        409: COMMON_ERROR_RESPONSES[409],
    },
)
async def confirmation_reply(
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    req: ChatPlannerReplyRequest = Body(...),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
    events_svc: ChatEventsService = Depends(get_events_service),
):
    """统一确认回复：收到 confirmation_request（planner / ask_human）时，调用本接口传入用户回复，agent 会继续执行。"""
    sid = session_id.strip()
    if not chat_svc.can_access_session(sid, user_id):
        raise ForbiddenErrorResponse(msg='无权限访问该会话')
    reply_queue = stream_svc.get_reply_queue(sid)
    if reply_queue is None:
        raise ConflictErrorResponse(
            msg='当前无活跃任务，或任务已结束',
        )
    content = (req.content or '').strip()
    # 先广播 confirmation_reply，再 put_content 唤醒 agent，保证订阅流上顺序为 confirmation_request -> confirmation_reply -> tool_result
    stream_svc.broadcast_reply(sid, content)
    reply_queue.put_content(content)
    payload = {
        'source': 'User',
        'type': 'confirmation_reply',
        'content': content,
        'session_id': sid,
    }
    run_ctx = stream_svc.get_run_context(sid)
    if run_ctx:
        payload['task_id'] = run_ctx.get('task_id')
        payload['invocation_id'] = run_ctx.get('invocation_id')
    events_svc.add_history_event(sid, payload, user_id=user_id)
    return BaseResponse(msg='ok')


@router.get(
    '/{session_id}/share',
    response_model=ShareStatusApiResponse,
    summary='查询会话分享状态',
    description='查看会话是否已开启分享。已分享时任何人可查看；未分享时需为会话所有者。',
    operation_id='getChatSessionShareStatus',
    responses={
        403: COMMON_ERROR_RESPONSES[403],
    },
)
def get_share_status(
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    user_id: str | None = Depends(UserService.optional_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """查看分享状态。已分享时会话任何人可查看；未分享时需为会话所有者。"""
    status = chat_svc.get_share_status(session_id)
    if not status['enabled'] and not chat_svc.can_access_session(session_id, user_id):
        raise ForbiddenErrorResponse(msg='无权限查看该会话')
    return ShareStatusApiResponse(
        data=ShareStatusData(enabled=status['enabled']),
    )


@router.put(
    '/{session_id}/share',
    response_model=ShareStatusApiResponse,
    summary='设置会话分享状态',
    description='仅会话所有者可设置。开启分享后，其他人可在未登录场景下访问该会话的分享能力。',
    operation_id='setChatSessionShareStatus',
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def set_share_status(
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    body: ShareSetRequest = Body(...),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """设置分享状态。仅会话所有者可设置。开启后，stream 等接口可不鉴权访问。"""
    if not chat_svc.set_share_status(session_id, enabled=body.enabled, user_id=user_id):
        raise NotFoundErrorResponse(
            msg='Session not found or you are not the owner',
        )
    return ShareStatusApiResponse(
        data=ShareStatusData(enabled=body.enabled),
    )


@router.delete(
    '/{session_id}',
    response_model=BaseResponse,
    summary='删除会话',
    description='仅会话所有者可删除；关联聊天事件会随会话级联删除。',
    operation_id='deleteChatSession',
    responses={
        401: COMMON_ERROR_RESPONSES[401],
        404: COMMON_ERROR_RESPONSES[404],
    },
)
def delete_session(
    session_id: str = Path(..., description='会话 ID', examples=['session-001']),
    user_id: str = Depends(UserService.require_user_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
):
    """删除会话。仅会话所有者可删除；关联的聊天事件会随会话级联删除。"""
    sid = session_id.strip()
    if not chat_svc.delete_session(sid, user_id=user_id):
        raise NotFoundErrorResponse(
            msg='Session not found or you are not the owner',
        )
    return BaseResponse(msg='ok')
