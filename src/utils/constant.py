import os

import pymysql
from dotenv import find_dotenv, load_dotenv

load_dotenv()
CURRENT_ENV = os.getenv('SERVICE_ENV', 'test')
load_dotenv(find_dotenv(f'.env.{CURRENT_ENV}'))

AG_UI_EVENT = 'ag-ui'

DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', 'password'),
    'database': os.getenv('MYSQL_DATABASE', 'matmaster'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,  # 直接返回字典格式
    'autocommit': False,
}

# Redis（多 worker 时用于跨进程停止会话：Pub/Sub）
# 未配置则 stop 仅在本进程生效。配 REDIS_URL，例：redis://:密码@host:6379/0
REDIS_URL = (os.getenv('REDIS_URL') or '').strip() or None

BI_URL = os.getenv('BI_URL', 'https://account.test.dp.tech')

# 配额服务（与 MatMaster 一致：matmaster-tools-server）
_URL_PART = f'.{CURRENT_ENV}' if CURRENT_ENV and CURRENT_ENV != 'prod' else ''
MATMASTER_TOOLS_SERVER = os.getenv(
    'MATMASTER_TOOLS_SERVER',
    f'https://matmaster-tools-server{_URL_PART}.bohrium.com',
)

# Bohrium 节点 Open API（创建/列表/删除节点），test 环境走 openapi.test.dp.tech
BOHRIUM_OPENAPI_BASE_URL = os.getenv(
    'BOHRIUM_BASE_URL',
    (
        f'https://openapi{_URL_PART}.dp.tech/openapi/v1'
        if _URL_PART
        else 'https://open.bohrium.com/openapi/v1'
    ),
)

# Bohrium Core API（如 ak/list 根据 user_id + org_id 获取 access_key）
BOHRIUM_CORE_BASE_URL = os.getenv(
    'BOHRIUM_CORE_BASE_URL',
    (
        f'https://bohrium-core{_URL_PART}.dp.tech'
        if _URL_PART
        else 'https://bohrium-core.dp.tech'
    ),
)

# Bohrium 节点默认镜像 ID，按环境区分（创建节点时未指定且无 BOHRIUM_IMAGE_ID 时使用）
BOHRIUM_ENV_DEFAULT_IMAGE_IDS: dict[str, int] = {
    'test': 48925,
    'uat': 1509,
    'prod': 121443,
}
BOHRIUM_DEFAULT_IMAGE_ID = (
    BOHRIUM_ENV_DEFAULT_IMAGE_IDS.get(CURRENT_ENV)
    or BOHRIUM_ENV_DEFAULT_IMAGE_IDS['test']
)

# 会话历史注入（用于多轮上下文连续性）
_ENV_TRUE_VALUES = ('1', 'true', 'yes', 'on')
CTX_INJECTION_ENABLED = (
    os.getenv('CTX_INJECTION_ENABLED', 'true').strip().lower() in _ENV_TRUE_VALUES
)
CTX_HISTORY_MAX_LINES = int(os.getenv('CTX_HISTORY_MAX_LINES', '20'))
CTX_HISTORY_MAX_CHARS = int(os.getenv('CTX_HISTORY_MAX_CHARS', '4000'))
CTX_TOTAL_PROMPT_MAX_CHARS = int(os.getenv('CTX_TOTAL_PROMPT_MAX_CHARS', '12000'))
CTX_MAX_TOKENS_LIMIT = int(os.getenv('CTX_MAX_TOKENS_LIMIT', '128000'))
CTX_EVENT_WINDOW = int(os.getenv('CTX_EVENT_WINDOW', '200'))
CTX_FILTER_NOISE_ENABLED = (
    os.getenv('CTX_FILTER_NOISE_ENABLED', 'true').strip().lower() in _ENV_TRUE_VALUES
)
