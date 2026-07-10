"""配额服务：调用 MatMaster 平台的 quota 接口。

- check_quota_status: 发送前查询余额（GET /api/v1/quota/info）。
  计价化后只读金额额度 ``credit_remaining``（元）与 ``credit_reset_at``
  （下次额度刷新日期）；旧的次数 ``remaining`` 字段已不再使用。

  分层计费：MatMaster 平台启用真实光子后，还会附带 ``photon_remaining``（光子余额）
  与 ``photon_overflow_enabled``（用户光子代扣偏好，opt-in）。项目扣费上线后，
  调用方传 project_id 时再附带 ``org_wallet_pass``（项目扣费能否兜底，平台聚合判定）。
  发送前闸口语义与实扣瀑布同序（免费额度 -> 项目 -> 光子）：「免费额度耗尽，且项目
  扣费兜不了底，且（用户没开光子代扣 或 光子也耗尽）才拦截」。光子代扣是 opt-in：
  没开代扣时实扣侧会 skip，故闸口必须同时看 overflow 偏好，否则会把这类用户误放行
  导致漏扣。未启用光子时 photon_remaining 为 None、未传 project 时 org_wallet_pass
  为 False，闸口逐级退化，行为与之前一致。

扣费由 billing usage 上报在 MatMaster 平台侧按金额实时完成，evo 不再做按次扣减
（已移除 use_quota）；模型级次数限制并入金额额度（已移除 check_model_quota）。

异常不在此处捕获，由调用方/全局 error handler 统一处理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from clients.matmaster_platform.quota import fetch_quota_info

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class QuotaStatus:
    """发送前配额状态。

    remaining_yuan: 剩余金额额度（元）。
    reset_at: 下次额度刷新日期（ISO，如 ``2026-06-09``），无则 None。
    photon_remaining: 真实光子余额；MatMaster 平台未启用光子或查询失败为 None。
    photon_overflow_enabled: 用户是否开启「免费额度耗尽后扣光子」代扣偏好（opt-in，
        默认 False）。仅当为 True 时光子余额才计入放行——与平台实扣侧
        （PhotonSource 未开偏好则 skip）口径一致，避免「有光子但没开代扣」被误放行后漏扣。
    settlement_blocked: MatMaster 平台判定存在未清偿欠费等结算阻断时为 True。
        **结算阻断以本字段为唯一依据**：欠费清偿批量化后，平台在「在途清偿已
        覆盖全部欠费」时即置 False，此时 debt_micro 仍是 >0 的事实值
        （等批量 worker 落账才降）。
    debt_micro: 未清偿欠费的事实值（micro CNY），仅作展示/日志参考，不参与闸口——
        叠加 debt_micro>0 判阻断会把平台的先行解锁抵消，让用户空等批量落账时延。
    """

    remaining_yuan: float
    reset_at: str | None = None
    photon_remaining: float | None = None
    photon_overflow_enabled: bool = False
    debt_micro: int = 0
    settlement_blocked: bool = False
    settlement_block_reason: str | None = None
    org_wallet_pass: bool = False
    """项目扣费此刻能否兜底（平台按 project 判定：Nacos 开 + 用户偏好开 +
    项目 org 解析成功 + org 钱包余额（减在途预留）>0 + 成员门放行）。

    只有调用方传了 project_id 平台才会计算；缺失/未传/平台故障一律 False
    （fail-closed：闸口少放行不会多扣钱）。实扣瀑布是 免费额度 -> 项目 -> 光子，
    闸口据此在免费额度耗尽后放行「项目付钱」的用户——否则没光子（或没开光子
    代扣）的项目用户会被拦在发送前，org_wallet 源根本没机会出手。"""
    available_micro: int | None = None
    """可用额度（micro CNY）= 免费额度 +（仅开代扣）光子折算，与发送前闸口/实扣同口径。

    仅供「一次 run 内成本熔断」取预算快照用，不参与发送前闸口判定（is_exhausted）。
    旧平台接口不返回该字段时为 None（调用方据此关闭熔断、退化为只靠发送前闸口）。
    """

    @property
    def is_exhausted(self) -> bool:
        """额度是否耗尽（拦截发送）。

        分层闸口：免费金额额度 <= 0 时，仅当「用户开启了光子代扣偏好 **且** 仍有光子
        余额（> 0）」才不拦截（溢出走光子）。光子代扣是 opt-in：平台虽启用光子、账上
        也有余额，但用户没开代扣时实扣侧会 skip，闸口若只看 photon_remaining 放行就会
        漏扣，故必须同时要求 photon_overflow_enabled。未启用光子 / 没开代扣 / 余额为 0
        时退化为只看金额额度。

        结算阻断只看 settlement_blocked，不叠加 debt_micro>0（见字段说明）。
        """
        if self.settlement_blocked:
            return True
        if self.remaining_yuan > 0:
            return False
        # 与实扣瀑布同序（免费额度 -> 项目 -> 光子）：项目扣费能兜底就放行。
        # org_wallet_pass 已在平台侧聚合全部判据（含用户偏好与成员门），这里不再拆开看。
        if self.org_wallet_pass:
            return False
        if (
            self.photon_overflow_enabled
            and self.photon_remaining is not None
            and self.photon_remaining > 0
        ):
            return False
        return True

    def exhausted_message(self, fallback: str) -> str:
        """额度耗尽时的用户提示文案。

        有刷新日期则带出恢复时间；否则用调用方给的兜底措辞
        （网页端、飞书端等差异在此参数化）。
        """
        if self.settlement_blocked:
            return "存在未清偿账单，请先完成扣费后再继续使用。"
        if self.reset_at:
            return f"免费额度已用完，将于 {self.reset_at} 恢复。"
        return fallback


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


async def check_quota_status(
    user_id: str, project_id: int | str | None = None
) -> QuotaStatus:
    """查询用户剩余金额额度 + 下次刷新时间。

    只读 ``credit_remaining``（金额，元）；缺失或非法时按 0 处理（视为额度耗尽）。
    请求异常向上抛出。

    project_id 可选：传入时平台附带项目扣费判定 org_wallet_pass（见字段说明），
    并把项目可用余额并入 available_micro（熔断预算自动受益，无需单独处理）。
    调用方能拿到会话归属就应该传——不传等于放弃「项目付钱」用户的闸口放行。
    """
    inner = await fetch_quota_info(user_id, project_id=project_id)
    credit = _coerce_number(inner.get("credit_remaining"))
    remaining = max(0.0, credit) if credit is not None else 0.0
    reset_at = inner.get("credit_reset_at")
    reset_at = reset_at if isinstance(reset_at, str) and reset_at else None
    # 光子余额为可选字段：缺失（未启用）保持 None，闸口退化为只看金额额度。
    photon_remaining = _coerce_number(inner.get("photon_remaining"))
    # 光子代扣偏好（opt-in，默认 False）：缺失/非法按 False，闸口不把光子计入放行。
    photon_overflow_enabled = bool(inner.get("photon_overflow_enabled"))
    debt_raw = inner.get("debt_micro")
    debt_micro = int(debt_raw) if isinstance(debt_raw, int) else 0
    # 阻断只信平台的 settlement_blocked：批量清偿时代 debt_micro 是事实欠费，
    # 在途清偿覆盖后平台会先行解锁而 debt_micro 仍 >0（等 worker 落账），叠加
    # debt_micro>0 兜底会把解锁抵消。不需要按 debt_micro 做缺字段回退：平台侧
    # 两字段同一提交引入（tools-server 100aeb3），「有 debt 无 blocked」的版本
    # 不存在；更老的版本两个字段都没有，本就无债可拦。
    settlement_blocked = bool(inner.get("settlement_blocked"))
    settlement_block_reason = inner.get("settlement_block_reason")
    settlement_block_reason = (
        settlement_block_reason
        if isinstance(settlement_block_reason, str) and settlement_block_reason
        else None
    )
    # 可用额度（micro）：旧平台接口不返回则为 None（关闭 in-run 熔断）。
    available_raw = inner.get("available_micro")
    available_micro = int(available_raw) if isinstance(available_raw, int) else None
    # 项目扣费兜底判定：缺失/非法按 False（旧平台接口没有该字段 = 不放行）。
    org_wallet_pass = bool(inner.get("org_wallet_pass"))
    logger.info(
        "check_quota_status response: user_id=%s remaining=%s reset_at=%s "
        "photon_remaining=%s photon_overflow_enabled=%s debt_micro=%s "
        "settlement_blocked=%s org_wallet_pass=%s project_id=%s",
        user_id,
        remaining,
        reset_at,
        photon_remaining,
        photon_overflow_enabled,
        debt_micro,
        settlement_blocked,
        org_wallet_pass,
        project_id,
    )
    return QuotaStatus(
        remaining_yuan=remaining,
        reset_at=reset_at,
        photon_remaining=photon_remaining,
        photon_overflow_enabled=photon_overflow_enabled,
        debt_micro=debt_micro,
        settlement_blocked=settlement_blocked,
        settlement_block_reason=settlement_block_reason,
        org_wallet_pass=org_wallet_pass,
        available_micro=available_micro,
    )
