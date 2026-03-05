import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.apis.api_router import api_router
from src.base.base_res import BaseResponse
from src.models.health import HealthResponse
from src.models.root import RootResponse
from src.services.agent_run_service import get_agent_run_service, init_playground
from src.services.sessions_service import get_sessions_service
from src.services.user_service import get_user_service
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import CURRENT_ENV, DB_CONFIG
from src.utils.exceptions import BaseErrorResponse
from src.utils.logger import LoggingConfig, setup_logging
from src.utils.worker_id import get_worker_id

log_config = LoggingConfig.get_main_app_config()
setup_logging(**log_config)
logger = logging.getLogger(__name__)
logger.info('SERVICE_ENV=%s', CURRENT_ENV)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # 不再在启动时全局把 active 置为 idle：以便客户端重连时在 subscribe 流里能检测到
    # 「DB 为 active 但本进程未在跑」的 stale 会话，推送 run_interrupted 并可选重跑。
    # MatMaster Chat：提前初始化 playground，首条 /chat/send 无需等待
    try:
        await init_playground()
        logger.info('MatMaster chat playground initialized in lifespan.')
    except Exception as e:
        logger.warning('MatMaster chat playground init skipped in lifespan: %s', e)
    # 多 worker 时：Redis 订阅线程，使任意 worker 收到的 stop 能通知到跑 run 的 worker
    try:
        if get_sessions_service().start_redis_stop_subscriber():
            logger.info('Redis stop subscriber started in lifespan.')
    except Exception as e:
        logger.warning('Redis stop subscriber start skipped: %s', e)
    # worker 存活心跳：供 session run owner 区分「别的 pod 在跑」与「重启后旧 pid」
    worker_heartbeat_task = None

    async def _worker_heartbeat() -> None:
        interval = 10.0
        while True:
            await asyncio.sleep(interval)
            try:
                get_worker_registry_service().set_worker_alive(get_worker_id())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug('Worker heartbeat skipped: %s', e)

    try:
        get_worker_registry_service().set_worker_alive(get_worker_id())
        worker_heartbeat_task = asyncio.create_task(_worker_heartbeat())
        logger.info('Worker heartbeat task started in lifespan.')
    except Exception as e:
        logger.warning('Worker heartbeat start skipped: %s', e)
    yield
    if worker_heartbeat_task is not None:
        worker_heartbeat_task.cancel()
        try:
            await worker_heartbeat_task
        except asyncio.CancelledError:
            pass
    # 优雅退出：最多等待 30s 让当前 agent 任务结束，再关闭线程池
    try:
        svc = get_agent_run_service()
        executor = svc.get_executor()
        logger.info('Shutting down: waiting for agent executor (max 30s)...')
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: executor.shutdown(wait=True)),
                timeout=30.0,
            )
            logger.info('Agent executor shut down.')
        except asyncio.TimeoutError:
            logger.warning(
                'Agent executor shutdown timed out after 30s, proceeding with exit.'
            )
    except Exception as e:
        logger.warning('Graceful shutdown skip: %s', e)


app = FastAPI(
    title='MatMaster-Evo',
    description='MatMaster-Evo 后端服务',
    version='1.0.0',
    lifespan=lifespan,
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # 生产环境应该配置具体域名
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix='/api/v1')


@app.get('/', tags=['系统状态'])
async def root():
    """根端点"""
    return RootResponse(data={'description': 'MatMaster-Evo Service'})


# 健康检查（无需认证）
@app.get('/api/health', tags=['health'])
async def health_check() -> HealthResponse:
    """健康检查端点"""
    try:
        return HealthResponse(
            data={
                'status': 'healthy',
                'timestamp': datetime.utcnow().isoformat(),
                'components': {
                    'database': 'healthy',
                },
            },
        )
    except Exception as e:
        logger.error(f'Error: {str(e)}, {DB_CONFIG}')
        raise HTTPException(status_code=503, detail=f"服务不可用: {str(e)}")


# 中间件：记录请求日志
@app.middleware('http')
async def log_requests(request: Request, call_next):
    SENSITIVE_KEYS = {
        'password',
        'passwd',
        'token',
        'access_token',
        'refresh_token',
        'api_key',
        'secret',
    }
    MAX_BODY_LEN = 2000  # 避免日志爆炸

    def _redact(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                    out[k] = '***'
                else:
                    out[k] = _redact(v)
            return out
        if isinstance(obj, list):
            return [_redact(x) for x in obj]
        return obj

    user_id = get_user_service().get_user_id(request, required=False)
    start = time.perf_counter()
    query = dict(request.query_params)
    path_params = request.scope.get('path_params') or {}

    body_for_log = None
    content_type = (request.headers.get('content-type') or '').lower()
    # 注意：读取 body 会把整个请求体读入内存；只对 JSON 且长度可控时记录
    if (
        request.method in {'POST', 'PUT', 'PATCH'}
        and 'application/json' in content_type
    ):
        body_bytes = (
            await request.body()
        )  # Starlette 会缓存，后续 request.json()/Body 仍可用
        if body_bytes:
            text = body_bytes.decode('utf-8', errors='replace')
            text = text[:MAX_BODY_LEN]
            try:
                body_for_log = _redact(json.loads(text))
            except json.JSONDecodeError:
                body_for_log = text  # 非法 JSON 就记截断文本

    try:
        response = await call_next(request)
        return response
    finally:
        cost_ms = (time.perf_counter() - start) * 1000
        if request.url.path != '/api/health':
            logger.info(
                '%s %s %s - %s user_id=%s query=%s path_params=%s body=%s',
                request.method,
                request.url.path,
                getattr(locals().get('response', None), 'status_code', None),
                f'{cost_ms:.2f}ms',
                user_id,
                query,
                path_params,
                body_for_log,
            )


@app.exception_handler(BaseErrorResponse)
async def base_error_handler(request: Request, exc: BaseErrorResponse):
    payload = BaseResponse(code=exc.code, msg=exc.msg, data=exc.data)
    return JSONResponse(status_code=exc.http_status, content=payload.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    payload = BaseResponse(code=500, msg='internal error', data=None)
    return JSONResponse(status_code=500, content=payload.model_dump())


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'app:app',
        host='0.0.0.0',
        port=8000,
        reload=True,  # 开发时开启热重载
        log_level='info',
    )
