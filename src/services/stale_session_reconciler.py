"""Monitor 侧 stale session reconciler。

定期复核 DB 中仍标记 active/waiting 的 session 与 Redis 运行态是否一致。
多 Pod monitor 通过 Redis lease lock 串行执行；每条 session 修复前再做 Redis
二次确认，并使用 DB 条件更新避免扫描期间状态变化导致误修复。
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from src.utils.constant import env_int
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)

_LOCK_KEY = "matmaster:monitor:stale_session_reconciler:lock"


@dataclass(frozen=True)
class StaleSessionReconcilerConfig:
    enabled: bool = True
    batch_size: int = 100
    min_age_seconds: int = 120
    lock_ttl_seconds: int = 90

    @classmethod
    def from_env(cls) -> StaleSessionReconcilerConfig:
        enabled_raw = os.getenv("STALE_SESSION_RECONCILER_ENABLED", "true").strip()
        return cls(
            enabled=enabled_raw.lower() in {"1", "true", "yes", "on"},
            batch_size=env_int("STALE_SESSION_RECONCILER_BATCH_SIZE", 100),
            min_age_seconds=env_int("STALE_SESSION_RECONCILER_MIN_AGE_SEC", 120),
            lock_ttl_seconds=env_int("STALE_SESSION_RECONCILER_LOCK_TTL_SEC", 90),
        )


def _build_run_interrupted_message(reason: str) -> str:
    if reason == "restart":
        return "上一轮任务因服务重启中断，请重新发送以继续。"
    if reason == "deploy":
        return "上一轮任务因服务升级中断，请重新发送以继续。"
    return "上一轮任务因服务部署/重启中断，请重新发送以继续。"


class StaleSessionReconciler:
    """单轮 session 状态修复任务；tick() 自吞异常，适合作为 monitor job。"""

    def __init__(
        self,
        *,
        sessions_table: Any | None = None,
        events_service: Any | None = None,
        deploy_state_service: Any | None = None,
        redis: Any | None = None,
        registry: Any | None = None,
        cfg: StaleSessionReconcilerConfig | None = None,
    ) -> None:
        self._sessions_table = sessions_table
        self._events_service = events_service
        self._deploy_state_service = deploy_state_service
        self._redis = redis
        self._registry = registry
        self._cfg = cfg if cfg is not None else StaleSessionReconcilerConfig.from_env()

    def _ensure_deps(self) -> None:
        if self._sessions_table is None:
            from src.dao.chat_sessions_table import get_chat_sessions_table

            self._sessions_table = get_chat_sessions_table()
        if self._events_service is None:
            from src.services.events_service import get_events_service

            self._events_service = get_events_service()
        if self._deploy_state_service is None:
            from src.services.deploy_state_service import get_deploy_state_service

            self._deploy_state_service = get_deploy_state_service()
        if self._redis is None:
            from src.dao.redis_dao import get_redis_dao

            self._redis = get_redis_dao()
        if self._registry is None:
            from src.services.worker_registry_service import get_worker_registry_service

            self._registry = get_worker_registry_service()

    def tick(self) -> dict[str, int]:
        summary = {
            "enabled": int(self._cfg.enabled),
            "scanned": 0,
            "fixed_active": 0,
            "fixed_waiting": 0,
            "skipped_live": 0,
            "skipped_status": 0,
            "skipped_lock": 0,
            "skipped_redis": 0,
            "errors": 0,
            "tick_failed": 0,
        }
        if not self._cfg.enabled:
            return summary
        lock_token = f"{get_worker_id()}:{uuid.uuid4()}"
        lock_acquired = False
        try:
            self._ensure_deps()
            reserved = self._redis.try_reserve_nx(
                _LOCK_KEY, lock_token, self._cfg.lock_ttl_seconds
            )
            if reserved is None:
                summary["skipped_redis"] = 1
                return summary
            if reserved is False:
                summary["skipped_lock"] = 1
                return summary
            lock_acquired = True

            rows = self._sessions_table.list_stale_reconcile_candidates(
                limit=self._cfg.batch_size,
                min_age_seconds=self._cfg.min_age_seconds,
            )
            summary["scanned"] = len(rows)
            for row in rows:
                try:
                    self._process_row(row, summary)
                except Exception:  # noqa: BLE001
                    summary["errors"] += 1
                    logger.warning(
                        "stale session reconcile failed session_id=%s",
                        row.get("session_id"),
                        exc_info=True,
                    )
            return summary
        except Exception:  # noqa: BLE001
            summary["tick_failed"] = 1
            logger.warning("stale session reconciler tick failed", exc_info=True)
            return summary
        finally:
            if lock_acquired:
                self._redis.release_reservation(_LOCK_KEY, lock_token)

    def _process_row(self, row: dict[str, Any], summary: dict[str, int]) -> None:
        session_id = str(row.get("session_id") or "").strip()
        status = str(row.get("status") or "").strip()
        if not session_id or status not in {"active", "waiting"}:
            summary["skipped_status"] += 1
            return

        owner = self._registry.get_session_run_owner(session_id)
        owner_alive = bool(owner and self._registry.is_worker_alive(owner))
        queued = bool(self._redis.is_session_run_queued(session_id))

        if status == "active":
            if owner_alive or queued:
                summary["skipped_live"] += 1
                return
            if self._sessions_table.set_session_status_if_current(
                session_id, current_status="active", new_status="failed"
            ):
                self._registry.delete_session_run_owner(session_id)
                self._add_run_interrupted_event(row)
                summary["fixed_active"] += 1
            else:
                summary["skipped_status"] += 1
            return

        if queued or owner_alive:
            summary["skipped_live"] += 1
            return
        if self._sessions_table.set_session_status_if_current(
            session_id, current_status="waiting", new_status="idle"
        ):
            self._registry.delete_session_run_owner(session_id)
            summary["fixed_waiting"] += 1
        else:
            summary["skipped_status"] += 1

    def _add_run_interrupted_event(self, row: dict[str, Any]) -> None:
        session_id = str(row.get("session_id") or "").strip()
        reason, reason_meta = self._deploy_state_service.classify_restart_reason(
            session_id
        )
        content = _build_run_interrupted_message(reason)
        last_query = self._events_service.get_last_user_query(session_id)
        last_user_content = (last_query or {}).get("content", "")
        meta: dict[str, Any] = {}
        current_version = reason_meta.get("current_version")
        previous_version = reason_meta.get("previous_version")
        if current_version:
            meta["current_version"] = current_version
        if previous_version:
            meta["previous_version"] = previous_version
        if reason_meta.get("note"):
            meta["reason_note"] = reason_meta["note"]
        if reason in ("restart", "deploy"):
            meta["treat_as_failure"] = True
        history_content = {
            "message": content,
            "reason": reason,
            "last_user_content": last_user_content,
            **meta,
        }
        task_id = str(row.get("last_task_id") or "").strip() or None
        self._events_service.add_history_event(
            session_id,
            {
                "source": "System",
                "type": "run_interrupted",
                "content": history_content,
                "session_id": session_id,
                "task_id": task_id,
            },
            user_id=str(row.get("user_id") or "").strip() or None,
        )
