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

from src.services.bohrium_poller import _env_int

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
            progress_segments=_env_int("BOHRIUM_DELIVERY_PROGRESS_SEGMENTS", 3),
            reservation_ttl=_env_int("BOHRIUM_DELIVERY_RESERVATION_TTL", 60),
            scan_limit=_env_int("BOHRIUM_DELIVERY_SCAN_LIMIT", 200),
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
