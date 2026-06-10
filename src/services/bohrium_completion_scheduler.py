"""Bohrium 作业完成调度器：无状态闭环的 monitor 侧（attempt）。

只回答一个问题：当前这些已终态、尚未交付给 agent 的作业，是否值得唤醒一次
agent run？不 poll 平台、不分析结果、不持有任何跨 tick 状态——唤醒决策仅从
bohrium_jobs 当前聚合快照推导（无 now、无持久调度态、enqueued 后不记录任何
状态：progress 是否"已发"由 worker ack 隐式表达）。

非 final 自动唤醒上界（per-invocation）= 1(first_failure) + N(progress_segments)，
与作业数无关：progress 阈值 step=ceil(total/N) 随 total 缩放，每次成功 progress
经 worker ack 至少消化 step 个 pending。
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

from src.models.chat import DeliverySpec
from src.utils.constant import env_int

logger = logging.getLogger(__name__)

_RESERVATION_KEY_PREFIX = "bohrium_delivery:"

_DELIVERY_SCOPE_SUFFIX = (
    "本轮交付为 session 级：context 中全部 pending_terminal 详情行与"
    "溢出 job_ids 均在本次确认范围内，请一并查看处理。"
)


class Reason(enum.IntEnum):
    """唤醒原因；数值即优先级，session 合并时取最高。"""

    PROGRESS = 1
    FIRST_FAILURE = 2
    FINAL = 3


@dataclass(frozen=True)
class SchedulerConfig:
    progress_segments: int = 3
    reservation_ttl: int = 60
    scan_limit: int = 200

    @classmethod
    def from_env(cls) -> SchedulerConfig:
        return cls(
            progress_segments=env_int("BOHRIUM_DELIVERY_PROGRESS_SEGMENTS", 3),
            reservation_ttl=env_int("BOHRIUM_DELIVERY_RESERVATION_TTL", 60),
            scan_limit=env_int("BOHRIUM_DELIVERY_SCAN_LIMIT", 200),
        )


def decide(unit: dict[str, Any], cfg: SchedulerConfig) -> Reason | None:
    """无状态判定单个 (session, invocation) 聚合单元，三条全 ledger 推导。

    优先级 final > first_failure > progress；不重复发无需记账：final 经 ack
    pending→0、first_failure 经 ack failed_handled>0、progress 经 ack 回落到
    step 之下。
    """
    if unit["pending_terminal"] == 0:
        return None
    if unit["active"] == 0:
        return Reason.FINAL
    if unit["failed_total"] > 0 and unit["failed_handled"] == 0:
        return Reason.FIRST_FAILURE
    step = (unit["total"] + cfg.progress_segments - 1) // cfg.progress_segments
    if unit["pending_terminal"] >= step:
        return Reason.PROGRESS
    return None


def render_prompt(
    reason: Reason,
    counts: dict[str, int],
    first_failed: dict[str, Any] | None = None,
) -> str:
    """渲染唤醒 prompt；counts 为 session 级合计（tick 时刻聚合，run 实际执行时
    可能已漂移——context 行才是权威，文案不做绝对化承诺）。"""
    if reason is Reason.FINAL:
        body = (
            "触发批次的全部 Bohrium 作业已结束："
            f"成功 {counts['succeeded']}/{counts['total']}，"
            f"失败 {counts['failed_total']}。请汇总结果并给出下一步。"
        )
    elif reason is Reason.FIRST_FAILURE:
        info = first_failed or {}
        job_id = info.get("job_id") or "unknown"
        job_name = info.get("job_name") or "-"
        status = info.get("status") or "failed"
        body = (
            f"Bohrium 作业 {job_id}（{job_name}）首次失败（{status}），"
            f"另有 {counts['active']} 个作业仍在运行。"
        )
    else:
        terminal = counts["total"] - counts["active"]
        body = (
            f"本会话又有 Bohrium 作业完成（已终态 {terminal}/{counts['total']}，"
            f"仍在运行 {counts['active']}）。请汇报进度。"
        )
    return body + _DELIVERY_SCOPE_SUFFIX


class BohriumCompletionScheduler:
    """单轮调度单元；依赖惰性构造（monitor 进程循环外建一次），tick() 自吞所有
    异常、绝不抛。identity/status/NX 三门只做尽力去重，跨进程无互斥保证
    （status 写入是无条件 UPDATE）：正确性依赖 monitor 单实例（replica=1）。"""

    def __init__(
        self,
        *,
        jobs_table: Any | None = None,
        sessions_service: Any | None = None,
        stream_service: Any | None = None,
        redis: Any | None = None,
        cfg: SchedulerConfig | None = None,
    ) -> None:
        self._jobs_table = jobs_table
        self._sessions_service = sessions_service
        self._stream_service = stream_service
        self._redis = redis
        self._cfg = cfg if cfg is not None else SchedulerConfig.from_env()

    def _ensure_deps(self) -> None:
        if self._jobs_table is None:
            from src.dao.bohrium_jobs_table import get_bohrium_jobs_table

            self._jobs_table = get_bohrium_jobs_table()
        if self._sessions_service is None:
            from src.services.sessions_service import get_sessions_service

            self._sessions_service = get_sessions_service()
        if self._stream_service is None:
            from src.services.stream_service import get_stream_service

            self._stream_service = get_stream_service()
        if self._redis is None:
            from src.dao.redis_dao import get_redis_dao

            self._redis = get_redis_dao()

    def tick(self) -> dict[str, int]:
        summary = {
            "scanned": 0,
            "eligible": 0,
            "triggered": 0,
            "skipped_identity": 0,
            "skipped_busy": 0,
            "skipped_failed": 0,
            "skipped_redis": 0,
            "errors": 0,
            "tick_failed": 0,
        }
        try:
            self._ensure_deps()
            units = self._jobs_table.scan_delivery_units(limit=self._cfg.scan_limit)
        except Exception:  # noqa: BLE001
            logger.warning("bohrium completion scheduler tick failed", exc_info=True)
            summary["tick_failed"] = 1
            return summary
        summary["scanned"] = len(units)

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for unit in units:
            key = (unit["user_id"], unit["org_id"], unit["session_id"])
            groups.setdefault(key, []).append(unit)

        failed_sessions: list[str] = []
        for (user_id, org_id, session_id), session_units in groups.items():
            try:
                self._process_session(
                    user_id,
                    org_id,
                    session_id,
                    session_units,
                    summary,
                    failed_sessions,
                )
            except Exception:  # noqa: BLE001
                summary["errors"] += 1
                logger.warning(
                    "bohrium completion scheduler session failed session_id=%s",
                    session_id,
                    exc_info=True,
                )
        if summary["skipped_failed"]:
            # 停摆唯一的发现通道（run 级失败不自动重试，下一次用户交互自愈）
            logger.warning(
                "bohrium delivery stalled on failed sessions "
                "(no auto-retry; next user interaction self-heals): %s",
                failed_sessions,
            )
        if summary["skipped_redis"]:
            logger.warning(
                "bohrium delivery reservation unavailable, skipped %d session(s) "
                "this tick (fail-closed; resumes when redis recovers)",
                summary["skipped_redis"],
            )
        return summary

    def _process_session(
        self,
        user_id: str,
        org_id: str,
        session_id: str,
        session_units: list[dict[str, Any]],
        summary: dict[str, int],
        failed_sessions: list[str],
    ) -> None:
        eligible: list[tuple[Reason, dict[str, Any]]] = []
        for unit in session_units:
            reason = decide(unit, self._cfg)
            if reason is not None:
                eligible.append((reason, unit))
        if not eligible:
            return
        summary["eligible"] += 1

        # (a) identity 门：扫描已在 SQL 层过滤 owner，此门只兜扫描到触发
        # 之间 owner 又变更的竞态窗口
        session = self._sessions_service.get_session(session_id)
        if (
            not session
            or str(session.get("user_id") or "") != user_id
            or str(session.get("org_id") or "") != org_id
        ):
            summary["skipped_identity"] += 1
            return

        # (b) status 门：仅 idle 放行（跨进程互斥的主门，DB 状态跨进程可见）；
        # 复用 (a) 已取的行，避免二次查库
        status = self._sessions_service.reconcile_waiting_status(
            session_id, session.get("status")
        )
        if status == "failed":
            summary["skipped_failed"] += 1
            failed_sessions.append(session_id)
            return
        if status != "idle":
            summary["skipped_busy"] += 1
            return

        # (c) NX 原子占位（fail-closed）：同 tick 多实例竞态的防御纵深。
        # row-id 高水位避免秒级 terminal_at 碰撞压住新完成作业；短 TTL 无需释放。
        max_row_id = max(u["max_pending_terminal_id"] for u in session_units)
        key = f"{_RESERVATION_KEY_PREFIX}{user_id}:{org_id}:{session_id}:{max_row_id}"
        reserved = self._redis.try_reserve_nx(
            key, "1", ttl_sec=self._cfg.reservation_ttl
        )
        if reserved is None:
            # Redis 不可用：NX 与 run 队列共用同一 Redis，放行产不出可用 run，
            # 只会残留孤儿 trigger 事件——skip 是背压而非关停
            summary["skipped_redis"] += 1
            return
        if reserved is False:
            summary["skipped_busy"] += 1
            return

        primary_reason, primary_unit = max(eligible, key=lambda e: e[0])
        counts = {
            "total": sum(u["total"] for u in session_units),
            "active": sum(u["active"] for u in session_units),
            "succeeded": sum(u["succeeded"] for u in session_units),
            "failed_total": sum(u["failed_total"] for u in session_units),
        }
        first_failed = None
        if primary_reason is Reason.FIRST_FAILURE:
            first_failed = self._jobs_table.get_first_pending_failed(
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
                invocation_key=primary_unit["invocation_key"],
            )
        prompt = render_prompt(primary_reason, counts, first_failed)
        # 不传 dedup_key：占位已由 NX 接管。多 invocation 不同 workspace 合并时
        # 只取 primary 的（已知限制，作业级信息仍在 context 行内可见）。
        res = self._stream_service.trigger_run(
            session_id,
            prompt,
            origin="bohrium_completion",
            workspace=primary_unit["workspace"],
            delivery=DeliverySpec(notify=primary_reason is Reason.FINAL),
        )
        if res.status == "enqueued":
            # 不记录任何状态：progress 是否「已发」由 worker ack 隐式表达
            summary["triggered"] += 1
        elif res.status == "busy":
            summary["skipped_busy"] += 1
        else:
            summary["errors"] += 1
