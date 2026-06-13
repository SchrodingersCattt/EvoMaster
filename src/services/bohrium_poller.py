"""Bohrium 后台轮询核心（monitor 进程的 tick 单元）。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from matmaster.bohrium.status import to_ledger_status
from matmaster.bohrium.types import BohriumContext, BohriumCredentials
from src.dao.bohrium_jobs_table import BohriumJobsTable
from src.utils.constant import env_int

logger = logging.getLogger(__name__)

_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 600
# 单 tick 内作业查询并发度；DAO 每调用独立连接，线程间无共享可变状态
_POLL_CONCURRENCY = 8


def compute_poll_backoff(poll_count: int) -> int:
    """按已完成 poll_count 计算退避：0->30, 1->60, 2->120, ... 封顶 600 秒。"""
    n = max(0, int(poll_count))
    return min(_BASE_BACKOFF_SECONDS * (2 ** min(n, 5)), _MAX_BACKOFF_SECONDS)


class BohriumJobPoller:
    def __init__(
        self,
        *,
        table: Any | None = None,
        get_access_key: Callable[[str, str], str | None] | None = None,
        get_job_detail: Callable[..., dict[str, Any]] | None = None,
        base_url: str | None = None,
        lost_after_seconds: int | None = None,
    ) -> None:
        self._table = table if table is not None else BohriumJobsTable()
        if get_access_key is None:
            from src.services.user_service import UserService

            get_access_key = UserService.get_existing_bohrium_access_key
        if get_job_detail is None:
            from matmaster.bohrium.client import get_job_detail as _get_job_detail

            get_job_detail = _get_job_detail
        if base_url is None:
            from matmaster.bohrium.endpoints import get_bohrium_base_url

            base_url = get_bohrium_base_url()
        self._get_access_key = get_access_key
        self._get_job_detail = get_job_detail
        self._base_url = base_url
        self._lost_after = (
            lost_after_seconds
            if lost_after_seconds is not None
            else env_int("BOHRIUM_POLL_LOST_AFTER_SECONDS", 86400)
        )

    def run_once(
        self, *, limit: int = 50, claim_timeout_seconds: int = 120
    ) -> dict[str, int]:
        claimed = self._table.claim_due_batch(
            limit=limit, claim_timeout_seconds=claim_timeout_seconds
        )
        if not claimed:
            return {"claimed": 0, "polled": 0, "errors": 0}
        ak_cache = self._prefetch_access_keys(claimed)
        with ThreadPoolExecutor(
            max_workers=min(_POLL_CONCURRENCY, len(claimed))
        ) as pool:
            outcomes = list(
                pool.map(lambda job: self._poll_one(job, ak_cache), claimed)
            )
        polled = sum(outcomes)
        return {
            "claimed": len(claimed),
            "polled": polled,
            "errors": len(outcomes) - polled,
        }

    def _prefetch_access_keys(
        self, claimed: list[dict[str, Any]]
    ) -> dict[tuple[str, str], str | None]:
        ak_cache: dict[tuple[str, str], str | None] = {}
        for job in claimed:
            key = (str(job["user_id"]), str(job["org_id"]))
            if key in ak_cache:
                continue
            try:
                ak_cache[key] = self._get_access_key(*key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "poller get_access_key failed user=%s org=%s",
                    *key,
                    exc_info=True,
                )
                ak_cache[key] = None
        return ak_cache

    def _poll_one(
        self, job: dict[str, Any], ak_cache: dict[tuple[str, str], str | None]
    ) -> bool:
        user_id = str(job["user_id"])
        org_id = str(job["org_id"])
        sandbox = bool(job["sandbox"])
        raw_job_id = str(job["job_id"])
        backoff = compute_poll_backoff(int(job.get("poll_count", 0)))

        access_key = ak_cache.get((user_id, org_id))
        if not access_key:
            logger.warning(
                "poller access_key unavailable user=%s org=%s job_id=%s",
                user_id,
                org_id,
                raw_job_id,
            )
            self._table.mark_poll_error(
                user_id=user_id,
                org_id=org_id,
                sandbox=sandbox,
                job_id=raw_job_id,
                backoff_seconds=backoff,
                lost_after_seconds=self._lost_after,
            )
            return False

        try:
            ctx = self._build_ctx(job, access_key)
            job_id: int | str = raw_job_id if sandbox else int(raw_job_id)
            detail = self._get_job_detail(ctx, job_id=job_id)
            code = detail.get("status") if isinstance(detail, dict) else None
            if code is None:
                logger.warning("poller detail missing status job_id=%s", raw_job_id)
                self._table.mark_poll_error(
                    user_id=user_id,
                    org_id=org_id,
                    sandbox=sandbox,
                    job_id=raw_job_id,
                    backoff_seconds=backoff,
                    lost_after_seconds=self._lost_after,
                )
                return False
            decision = to_ledger_status(int(code))
            self._table.apply_poll(
                user_id=user_id,
                org_id=org_id,
                sandbox=sandbox,
                job_id=raw_job_id,
                status=decision.status,
                is_terminal=decision.is_terminal,
                backoff_seconds=backoff,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "poller get_job_detail failed job_id=%s: %s",
                raw_job_id,
                exc,
                exc_info=True,
            )
            self._table.mark_poll_error(
                user_id=user_id,
                org_id=org_id,
                sandbox=sandbox,
                job_id=raw_job_id,
                backoff_seconds=backoff,
                lost_after_seconds=self._lost_after,
            )
            return False

    def _build_ctx(self, job: dict[str, Any], access_key: str) -> BohriumContext:
        cred = BohriumCredentials(
            access_key=access_key,
            project_id=int(job["project_id"]),
            user_id=None,
            user_no="",
            base_url=self._base_url,
        )
        return BohriumContext.from_credentials(
            cred, sandbox=bool(job["sandbox"]), source="poller"
        )


class BohriumMonitor:
    """Single Bohrium monitor tick unit for the external monitor process."""

    def __init__(
        self,
        *,
        poller: BohriumJobPoller | None = None,
        limit: int | None = None,
        claim_timeout_seconds: int | None = None,
    ) -> None:
        self._poller = poller
        self._limit = (
            limit if limit is not None else env_int("BOHRIUM_MONITOR_LIMIT", 50)
        )
        self._claim_timeout = (
            claim_timeout_seconds
            if claim_timeout_seconds is not None
            else env_int("BOHRIUM_MONITOR_CLAIM_TIMEOUT", 120)
        )

    def tick(self) -> dict[str, int]:
        """Run one monitor round and never let poller errors escape the loop."""
        try:
            if self._poller is None:
                self._poller = BohriumJobPoller()
            return self._poller.run_once(
                limit=self._limit,
                claim_timeout_seconds=self._claim_timeout,
            )
        except Exception:  # noqa: BLE001
            logger.warning("bohrium monitor tick failed", exc_info=True)
            return {"claimed": 0, "polled": 0, "errors": 0, "tick_failed": 1}
