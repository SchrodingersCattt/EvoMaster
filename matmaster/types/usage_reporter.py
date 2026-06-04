"""Narrow port for reporting per-call LLM usage and obtaining its cost.

``UsageCollectingProvider`` 通过该端口实时上报每次 LLM 调用的 usage 并取回当次
定价结果。具体实现（HTTP 上报 matmaster-tools-server）由入口注入，核心 provider
只依赖此端口，不依赖 ``clients`` / ``src``，保持 matmaster 的 import 隔离。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UsageReporter(Protocol):
    """上报一次 LLM 调用 usage 并返回定价结果的窄端口。"""

    async def report_call(
        self,
        *,
        call_index: int,
        spawn_id: str | None,
        model: str,
        usage: dict[str, Any],
    ) -> dict[str, Any] | None:
        """上报一次已完成的 LLM 调用 usage；返回当次定价 data（或 None）。

        返回 dict 透传自 tools-server ``UsageIngestData``，含 ``total_amount_micro``
        / ``total_amount_settle_micro`` / ``pricing_status`` 等字段。
        """
        ...
