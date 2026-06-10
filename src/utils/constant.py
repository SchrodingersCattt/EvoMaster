import logging
import os

import pymysql

from utils.env import SERVICE_ENV, URL_PART  # noqa: E402

logger = logging.getLogger(__name__)


def env_int(name: str, default: int) -> int:
    """读 int 环境变量；缺失或非法回退默认值。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("invalid int env %s=%r, using default %d", name, raw, default)
        return default


AG_UI_EVENT = "ag-ui"
BUILD_TRIGGER = "20260512-verify-skill-sync-2"

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "password"),
    "database": os.getenv("MYSQL_DATABASE", "matmaster"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,  # 直接返回字典格式
    "autocommit": False,
}

# Redis（多 worker 时用于跨进程停止会话：Pub/Sub）
# 未配置则 stop 仅在本进程生效。配 REDIS_URL，例：redis://:密码@host:6379/0
REDIS_URL = (os.getenv("REDIS_URL") or "").strip() or None
# 内部程序化触发鉴权 token（共享密钥，仅内网可达）。未配置则禁用 /stream 内部发起。
INTERNAL_TRIGGER_TOKEN = (os.getenv("INTERNAL_TRIGGER_TOKEN") or "").strip() or None

# 配额服务（与 MatMaster 一致：matmaster-tools-server；根 URL 见 ``utils.env``）
# 支持/通知服务（模板发送等），按环境：test -> support.test.dp.tech，uat -> support.uat.dp.tech，prod -> support.dp.tech
SUPPORT_SERVICE_BASE_URL = (
    os.getenv("SUPPORT_SERVICE_BASE_URL", "").strip()
    or (f"https://support{URL_PART}.dp.tech" if URL_PART else "https://support.dp.tech")
).rstrip("/") or None
# 会话完成邮件模板 ID（支持服务侧按环境注册不同 ID）
SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS = {"test": "140", "uat": "21", "prod": "116"}
SUPPORT_SESSION_COMPLETE_TEMPLATE_ID = SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS.get(
    SERVICE_ENV, SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS["test"]
)
# 账号/用户信息 API（如 account_api/users/{user_id} 查昵称、邮箱），按环境自动生成 host
ACCOUNT_API_BASE_URL = (
    f"https://account{URL_PART}.dp.tech" if URL_PART else "https://account.dp.tech"
).rstrip("/")

# Bohrium Open API 的 host（不含版本路径），请求时拼接 /openapi/v1 或 /openapi/v2
# 如 https://openapi.test.dp.tech；node 接口用 v1，image 接口用 v2
BOHRIUM_OPENAPI_HOST = os.getenv(
    "BOHRIUM_BASE_URL",
    (f"https://openapi{URL_PART}.dp.tech" if URL_PART else "https://open.bohrium.com"),
).rstrip("/")

# Bohrium Core API（ak/list 列举 AK；ak/add 在无可用 AK 时自动创建，与前端 bohrapi/v1/ak/* 一致）
BOHRIUM_CORE_BASE_URL = os.getenv(
    "BOHRIUM_CORE_BASE_URL",
    (
        f"https://bohrium-core{URL_PART}.dp.tech"
        if URL_PART
        else "https://bohrium-core.dp.tech"
    ),
)

# Bohrium 节点默认镜像 ID，按环境区分（创建节点时未指定且无 BOHRIUM_IMAGE_ID 时使用）
BOHRIUM_ENV_DEFAULT_IMAGE_IDS: dict[str, int] = {
    "test": 49074,
    "uat": 1587,
    "prod": 123104,
}
BOHRIUM_DEFAULT_IMAGE_ID = (
    BOHRIUM_ENV_DEFAULT_IMAGE_IDS.get(SERVICE_ENV)
    or BOHRIUM_ENV_DEFAULT_IMAGE_IDS["test"]
)

# 镜像 name 也在构建时写入仓库，运行期不用再调 image/private 获取
BOHRIUM_ENV_DEFAULT_IMAGE_NAMES: dict[str, str] = {
    "test": "matmaster:4d77eefb-20260521-100719",
    "uat": "matmaster:4d77eefb-20260521-100715",
    "prod": "matmaster:4d77eefb-20260521-100718",
}
BOHRIUM_DEFAULT_IMAGE_NAME = (
    BOHRIUM_ENV_DEFAULT_IMAGE_NAMES.get(SERVICE_ENV)
    or BOHRIUM_ENV_DEFAULT_IMAGE_NAMES["test"]
)
