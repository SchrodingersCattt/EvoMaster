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
