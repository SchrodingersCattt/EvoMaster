"""QuotaStatus 分层发送闸口语义（免费额度 + 真实光子）。"""

from __future__ import annotations

from src.services.quota_service import QuotaStatus


class TestIsExhausted:
    def test_free_credit_available_not_exhausted(self):
        assert QuotaStatus(remaining_yuan=1.0).is_exhausted is False

    def test_free_zero_no_photon_field_exhausted(self):
        # 未启用光子（photon_remaining 默认 None）：退化为只看金额额度。
        assert QuotaStatus(remaining_yuan=0.0).is_exhausted is True

    def test_free_zero_but_photon_available_not_exhausted(self):
        assert (
            QuotaStatus(remaining_yuan=0.0, photon_remaining=5.0).is_exhausted is False
        )

    def test_free_zero_and_photon_zero_exhausted(self):
        assert (
            QuotaStatus(remaining_yuan=0.0, photon_remaining=0.0).is_exhausted is True
        )

    def test_free_available_photon_zero_not_exhausted(self):
        assert (
            QuotaStatus(remaining_yuan=2.0, photon_remaining=0.0).is_exhausted is False
        )

    def test_exhausted_message_with_reset(self):
        status = QuotaStatus(remaining_yuan=0.0, reset_at='2026-06-09')
        assert '2026-06-09' in status.exhausted_message('fallback')

    def test_exhausted_message_fallback(self):
        status = QuotaStatus(remaining_yuan=0.0, reset_at=None)
        assert status.exhausted_message('用完啦') == '用完啦'
