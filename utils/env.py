"""仓库级 dotenv 引导 + ``MATMASTER_TOOLS_SERVER`` 常量。

先 ``load_dotenv``，然后读取 ``MATMASTER_TOOLS_SERVER``（用于评测接口）。
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

load_dotenv()

# Optional: URL of a self-hosted matmaster-tools-server for evaluation ingestion.
# Leave empty if not using the evaluation ingest API.
MATMASTER_TOOLS_SERVER = os.getenv("MATMASTER_TOOLS_SERVER", "")

# Bearer token for the evaluation ingest API (optional).
_eval_bearer = os.getenv("MATMASTER_TOOLS_EVALUATION_BEARER", "").strip()
MATMASTER_TOOLS_EVALUATION_BEARER: str | None = _eval_bearer or None

# 调用 matmaster-tools-server 评测接口（ingest、question-catalog 等）时的鉴权，与
# ``require_evaluation_access`` 中的服务密钥分支对齐（Nacos evaluation.service_api_keys）。
_eval_bearer = os.getenv("MATMASTER_TOOLS_EVALUATION_BEARER", "").strip()
MATMASTER_TOOLS_EVALUATION_BEARER: str | None = _eval_bearer or None
