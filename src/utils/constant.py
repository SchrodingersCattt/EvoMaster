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

# 配额服务（与 MatMaster 一致：matmaster-tools-server）
_URL_PART = f'.{CURRENT_ENV}' if CURRENT_ENV and CURRENT_ENV != 'prod' else ''
# 支持/通知服务（模板发送等），按环境：test -> support.test.dp.tech，uat -> support.uat.dp.tech，prod -> support.dp.tech
SUPPORT_SERVICE_BASE_URL = (
    os.getenv('SUPPORT_SERVICE_BASE_URL', '').strip()
    or (
        f'https://support{_URL_PART}.dp.tech'
        if _URL_PART
        else 'https://support.dp.tech'
    )
).rstrip('/') or None
# 会话完成邮件模板 ID（支持服务侧按环境注册不同 ID）
SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS = {'test': '140', 'uat': '21', 'prod': '116'}
SUPPORT_SESSION_COMPLETE_TEMPLATE_ID = SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS.get(
    CURRENT_ENV, SUPPORT_SESSION_COMPLETE_TEMPLATE_IDS['test']
)
# 账号/用户信息 API（如 account_api/users/{user_id} 查昵称、邮箱），按环境自动生成 host
ACCOUNT_API_BASE_URL = (
    f'https://account{_URL_PART}.dp.tech' if _URL_PART else 'https://account.dp.tech'
).rstrip('/')
MATMASTER_TOOLS_SERVER = os.getenv(
    'MATMASTER_TOOLS_SERVER',
    f'https://matmaster-tools-server{_URL_PART}.bohrium.com',
)

# Bohrium Open API 的 host（不含版本路径），请求时拼接 /openapi/v1 或 /openapi/v2
# 如 https://openapi.test.dp.tech；node 接口用 v1，image 接口用 v2
BOHRIUM_OPENAPI_HOST = os.getenv(
    'BOHRIUM_BASE_URL',
    (
        f'https://openapi{_URL_PART}.dp.tech'
        if _URL_PART
        else 'https://open.bohrium.com'
    ),
).rstrip('/')

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
    'test': 48969,
    'uat': 1554,
    'prod': 121870,
}
BOHRIUM_DEFAULT_IMAGE_ID = (
    BOHRIUM_ENV_DEFAULT_IMAGE_IDS.get(CURRENT_ENV)
    or BOHRIUM_ENV_DEFAULT_IMAGE_IDS['test']
)

# 镜像 name 也在构建时写入仓库，运行期不用再调 image/private 获取
BOHRIUM_ENV_DEFAULT_IMAGE_NAMES: dict[str, str] = {
    'test': 'matmaster:71fcdf5d-20260320-084108',
    'uat': 'matmaster:71fcdf5d-20260320-084101',
    'prod': 'matmaster:71fcdf5d-20260320-084101',
}
BOHRIUM_DEFAULT_IMAGE_NAME = (
    BOHRIUM_ENV_DEFAULT_IMAGE_NAMES.get(CURRENT_ENV)
    or BOHRIUM_ENV_DEFAULT_IMAGE_NAMES['test']
)
