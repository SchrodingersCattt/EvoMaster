import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.apis.api_router import api_router
from src.models.health import HealthResponse
from src.models.root import RootResponse
from src.utils.constant import DB_CONFIG
from src.utils.logger import LoggingConfig, setup_logging

log_config = LoggingConfig.get_main_app_config()
setup_logging(**log_config)
logger = logging.getLogger(__name__)

# 安全组件
security = HTTPBearer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # MatMaster Chat：提前初始化 playground，首条 /chat/send 无需等待
    try:
        from src.services.chat_service import init_playground

        await init_playground()
        logger.info('MatMaster chat playground initialized in lifespan.')
    except Exception as e:
        logger.warning('MatMaster chat playground init skipped in lifespan: %s', e)
    yield


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


# API密钥验证（简单版本）
def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # 从环境变量获取有效的API密钥
    valid_api_keys = os.getenv('API_KEYS', '').split(',')
    if credentials.credentials not in valid_api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail='无效的API密钥'
        )
    return credentials.credentials


# 中间件：记录请求日志
@app.middleware('http')
async def log_requests(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info(
        f"{request.client.host} - \"{request.method} {request.url.path} \" "
        f"{response.status_code} - {process_time:.3f}s"
    )

    return response


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


@app.get('/', tags=['系统状态'])
async def root():
    """根端点"""
    return RootResponse(data={'description': 'MatMaster-Evo Service'})


# 错误处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {str(exc)}")
    return JSONResponse(status_code=500, content={'code': -1, 'msg': str(exc)})


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'app:app',
        host='0.0.0.0',
        port=8000,
        reload=True,  # 开发时开启热重载
        log_level='info',
    )
