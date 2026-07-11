"""QuotaStatus 分层发送闸口语义（免费额度 + 真实光子）。"""

from __future__ import annotations

from src.services.quota_service import QuotaStatus


async def test_check_quota_status_maps_client_data(monkeypatch):
    from src.services import quota_service as mod

    async def _fake_fetch_quota_info(user_id, project_id=None):
        assert user_id == 'u1'
        return {
            'credit_remaining': 1.25,
            'credit_reset_at': '2026-06-09',
            'photon_remaining': 3,
            'photon_overflow_enabled': True,
            'available_micro': 1_250_000,
            'debt_micro': 0,
            'settlement_blocked': False,
        }

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1')

    assert status.remaining_yuan == 1.25
    assert status.reset_at == '2026-06-09'
    assert status.photon_remaining == 3.0
    assert status.photon_overflow_enabled is True
    assert status.available_micro == 1_250_000
    assert status.debt_micro == 0
    assert status.settlement_blocked is False


async def test_settlement_blocked_false_respected_even_with_debt(monkeypatch):
    # 批量清偿时代的核心语义：在途清偿覆盖全部欠费后平台先行解锁
    # （settlement_blocked=False 而 debt_micro 仍 >0），客户端必须尊重解锁，
    # 不得再用 debt_micro>0 兜底把用户拦回去空等 worker 落账。
    from src.services import quota_service as mod

    async def _fake_fetch_quota_info(user_id, project_id=None):
        return {
            'credit_remaining': 1.0,
            'debt_micro': 50_000,
            'settlement_blocked': False,
        }

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1')

    assert status.debt_micro == 50_000
    assert status.settlement_blocked is False
    assert status.is_exhausted is False


async def test_missing_settlement_blocked_defaults_unblocked(monkeypatch):
    # 缺失 settlement_blocked 按未阻断处理，不做 debt_micro>0 回退：平台侧两
    # 字段同一提交引入，「有 debt 无 blocked」的服务端版本不存在。
    from src.services import quota_service as mod

    async def _fake_fetch_quota_info(user_id, project_id=None):
        return {
            'credit_remaining': 1.0,
        }

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1')

    assert status.settlement_blocked is False
    assert status.is_exhausted is False


async def test_check_quota_status_maps_settlement_block(monkeypatch):
    from src.services import quota_service as mod

    async def _fake_fetch_quota_info(user_id, project_id=None):
        assert user_id == 'u1'
        return {
            'credit_remaining': 10.0,
            'photon_remaining': 100,
            'photon_overflow_enabled': True,
            'available_micro': 0,
            'debt_micro': 50_000,
            'settlement_blocked': True,
            'settlement_block_reason': 'debt_unpaid',
        }

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1')

    assert status.debt_micro == 50_000
    assert status.settlement_blocked is True
    assert status.settlement_block_reason == 'debt_unpaid'
    assert status.is_exhausted is True


class TestIsExhausted:
    def test_free_credit_available_not_exhausted(self):
        assert QuotaStatus(remaining_yuan=1.0).is_exhausted is False

    def test_debt_blocks_even_with_free_credit_and_photon(self):
        assert (
            QuotaStatus(
                remaining_yuan=1.0,
                photon_remaining=5.0,
                photon_overflow_enabled=True,
                debt_micro=100,
                settlement_blocked=True,
            ).is_exhausted
            is True
        )

    def test_debt_alone_does_not_block_when_unblocked(self):
        # 阻断只看 settlement_blocked：debt_micro 是事实欠费（可能已被在途
        # 清偿覆盖），不得叠加进闸口。
        assert (
            QuotaStatus(
                remaining_yuan=1.0,
                debt_micro=100,
                settlement_blocked=False,
            ).is_exhausted
            is False
        )

    def test_exhausted_message_ignores_debt_when_unblocked(self):
        status = QuotaStatus(
            remaining_yuan=0.0,
            reset_at='2026-06-09',
            debt_micro=100,
            settlement_blocked=False,
        )
        assert '未清偿账单' not in status.exhausted_message('fallback')
        assert '2026-06-09' in status.exhausted_message('fallback')

    def test_free_zero_no_photon_field_exhausted(self):
        # 未启用光子（photon_remaining 默认 None）：退化为只看金额额度。
        assert QuotaStatus(remaining_yuan=0.0).is_exhausted is True

    def test_free_zero_photon_available_and_overflow_enabled_not_exhausted(self):
        # 开了代扣且有光子余额：放行（溢出走光子）。
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                photon_remaining=5.0,
                photon_overflow_enabled=True,
            ).is_exhausted
            is False
        )

    def test_free_zero_photon_available_but_overflow_disabled_exhausted(self):
        # 口子 A 核心用例：有光子余额但没开代扣 -> 实扣侧会 skip，闸口必须拦截。
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                photon_remaining=5.0,
                photon_overflow_enabled=False,
            ).is_exhausted
            is True
        )

    def test_free_zero_photon_available_overflow_default_exhausted(self):
        # 偏好字段缺省（默认 False）时不把光子计入放行。
        assert (
            QuotaStatus(remaining_yuan=0.0, photon_remaining=5.0).is_exhausted is True
        )

    def test_free_zero_and_photon_zero_exhausted(self):
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                photon_remaining=0.0,
                photon_overflow_enabled=True,
            ).is_exhausted
            is True
        )

    def test_free_available_photon_zero_not_exhausted(self):
        assert (
            QuotaStatus(remaining_yuan=2.0, photon_remaining=0.0).is_exhausted is False
        )

    def test_available_micro_defaults_none_and_not_in_gate(self):
        # available_micro 仅供 in-run 成本熔断预算，不参与发送前闸口（is_exhausted）。
        assert QuotaStatus(remaining_yuan=1.0).available_micro is None
        assert (
            QuotaStatus(remaining_yuan=0.0, available_micro=5_000_000).is_exhausted
            is True
        )

    def test_exhausted_message_with_reset(self):
        status = QuotaStatus(remaining_yuan=0.0, reset_at='2026-06-09')
        assert '2026-06-09' in status.exhausted_message('fallback')

    def test_exhausted_message_fallback(self):
        status = QuotaStatus(remaining_yuan=0.0, reset_at=None)
        assert status.exhausted_message('用完啦') == '用完啦'


class TestOrgWalletGate:
    """项目扣费闸口：org_wallet_pass 在瀑布中的位置与退化行为。"""

    def test_org_wallet_pass_unblocks_user_without_photon(self):
        # 核心场景：没光子（或没开代扣）、靠项目付钱的用户，不得被拦在发送前。
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                photon_remaining=0.0,
                photon_overflow_enabled=False,
                org_wallet_pass=True,
            ).is_exhausted
            is False
        )

    def test_default_false_keeps_old_behavior(self):
        # 未传 project / 旧平台接口：org_wallet_pass 缺省 False，闸口行为不变。
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                photon_remaining=0.0,
                photon_overflow_enabled=True,
            ).is_exhausted
            is True
        )

    def test_settlement_block_beats_org_wallet(self):
        # 欠费阻断优先：项目能付新账不等于旧账清了。
        assert (
            QuotaStatus(
                remaining_yuan=0.0,
                org_wallet_pass=True,
                settlement_blocked=True,
            ).is_exhausted
            is True
        )


async def test_check_quota_status_parses_org_wallet_pass(monkeypatch):
    from src.services import quota_service as mod

    seen = {}

    async def _fake_fetch_quota_info(user_id, project_id=None):
        seen['project_id'] = project_id
        return {
            'credit_remaining': 0.0,
            'org_wallet_pass': True,
            'org_wallet_available_fen': 300,
        }

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1', project_id=12791)

    assert seen['project_id'] == 12791
    assert status.org_wallet_pass is True
    assert status.is_exhausted is False


async def test_check_quota_status_org_wallet_defaults_false(monkeypatch):
    # 平台没返回该字段（旧版本 / 没传 project）：按 False，不放行。
    from src.services import quota_service as mod

    async def _fake_fetch_quota_info(user_id, project_id=None):
        return {'credit_remaining': 0.0}

    monkeypatch.setattr(mod, 'fetch_quota_info', _fake_fetch_quota_info)

    status = await mod.check_quota_status('u1')

    assert status.org_wallet_pass is False
    assert status.is_exhausted is True
