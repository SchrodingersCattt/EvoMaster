"""兼容层：LLM 计费上报客户端的实现已下沉到 ``clients.billing.client``。

历史 import 路径 ``from src.services.billing_service import BillingService,
BillingRunContext, get_billing_service`` 继续可用；新代码请直接从
``clients.billing`` 导入。客户端被下沉到与 src 并列的 ``clients/`` 顶层，便于
matmaster.devshell / evaluation 在不反向 import src 的前提下复用（见
tests/matmaster/test_import_audit.py）。
"""

from __future__ import annotations

from clients.billing.client import (
    BillingRunContext,
    BillingService,
    get_billing_service,
)

__all__ = ["BillingRunContext", "BillingService", "get_billing_service"]
