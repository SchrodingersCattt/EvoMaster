"""仓库级 dotenv 引导 + 由 ``SERVICE_ENV`` 推出的常量（含 matmaster-tools-server 根 URL）。

与历史上 ``src.utils.constant`` 开头一致：先 ``load_dotenv``，再按 ``SERVICE_ENV`` 加载
``.env.<env>``，最后读取 ``SERVICE_ENV`` / ``URL_PART`` / ``MATMASTER_TOOLS_SERVER``。

``utils`` 包 ``__init__`` 会 import 本模块，保证任意 ``import utils...`` 时先完成上述步骤。
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

load_dotenv()
_SERVICE_ENV = os.getenv("SERVICE_ENV", "test")
load_dotenv(find_dotenv(f".env.{_SERVICE_ENV}"))

SERVICE_ENV = os.getenv("SERVICE_ENV", "test")
URL_PART = f".{SERVICE_ENV}" if SERVICE_ENV and SERVICE_ENV != "prod" else ""
MATMASTER_TOOLS_SERVER = os.getenv(
    "MATMASTER_TOOLS_SERVER",
    f"https://matmaster-tools-server{URL_PART}.bohrium.com",
)
BOHRIUM_OPENAPI_BASE_COM = os.getenv(
    "BOHRIUM_OPENAPI_BASE_COM",
    f"https://open{URL_PART}.bohrium.com" if URL_PART else "https://open.bohrium.com",
)

# 调用 matmaster-tools-server 评测接口（ingest、question-catalog 等）时的鉴权，与
# ``require_evaluation_access`` 中的服务密钥分支对齐（Nacos evaluation.service_api_keys）。
_eval_bearer = os.getenv("MATMASTER_TOOLS_EVALUATION_BEARER", "").strip()
MATMASTER_TOOLS_EVALUATION_BEARER: str | None = _eval_bearer or None

# 调用 matmaster-tools-server 计费 usage 上报接口时的服务 Bearer，与
# ``require_billing_ingest_access`` 中的服务密钥分支对齐（Nacos billing.service_api_keys）。
_billing_bearer = os.getenv("MATMASTER_TOOLS_BILLING_BEARER", "").strip()
MATMASTER_TOOLS_BILLING_BEARER: str | None = _billing_bearer or None
