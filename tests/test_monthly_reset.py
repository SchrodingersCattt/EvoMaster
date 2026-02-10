"""
简单的月度重置逻辑测试样例
"""

from datetime import date


def test_monthly_reset_logic():
    """测试月度重置判断逻辑"""

    def should_reset(last_d_quota_reset_date: date, today: date) -> bool:
        """
        判断是否应该重置（简化版逻辑）
        """
        if last_d_quota_reset_date.month == 12:
            one_month_after = date(
                last_d_quota_reset_date.year + 1, 1, last_d_quota_reset_date.day
            )
        else:
            one_month_after = date(
                last_d_quota_reset_date.year,
                last_d_quota_reset_date.month + 1,
                last_d_quota_reset_date.day,
            )

        # Only reset if today > one_month_after (more than one month has passed)
        return today > one_month_after

    # 测试用例 1: 正常情况 - 超过一个月，应该重置
    last_reset = date(2024, 1, 15)
    today = date(2024, 2, 16)
    result = should_reset(last_reset, today)
    print(
        f"测试1: last_reset={last_reset}, today={today}, 应该重置={result} (期望: True)"
    )
    assert result == True, '测试1失败：应该重置'  # noqa: E712

    # 测试用例 2: 边界情况 - 正好一个月，不应该重置
    last_reset = date(2024, 1, 15)
    today = date(2024, 2, 15)
    result = should_reset(last_reset, today)
    print(
        f"测试2: last_reset={last_reset}, today={today}, 应该重置={result} (期望: False)"
    )
    assert result == False, '测试2失败：不应该重置'  # noqa: E712

    # 测试用例 3: 边界情况 - 少于一个月，不应该重置
    last_reset = date(2024, 1, 15)
    today = date(2024, 2, 14)
    result = should_reset(last_reset, today)
    print(
        f"测试3: last_reset={last_reset}, today={today}, 应该重置={result} (期望: False)"
    )
    assert result == False, '测试3失败：不应该重置'  # noqa: E712

    # 测试用例 4: 跨年情况 - 12月到1月，超过一个月，应该重置
    last_reset = date(2023, 12, 15)
    today = date(2024, 1, 16)
    result = should_reset(last_reset, today)
    print(
        f"测试4: last_reset={last_reset}, today={today}, 应该重置={result} (期望: True)"
    )
    assert result == True, '测试4失败：应该重置'  # noqa: E712

    # 测试用例 5: 跨年边界 - 12月15日到1月15日，正好一个月，不应该重置
    last_reset = date(2023, 12, 15)
    today = date(2024, 1, 15)
    result = should_reset(last_reset, today)
    print(
        f"测试5: last_reset={last_reset}, today={today}, 应该重置={result} (期望: False)"
    )
    assert result == False, '测试5失败：不应该重置'  # noqa: E712

    # 测试用例 6: 多个月 - 超过两个月，应该重置
    last_reset = date(2024, 1, 10)
    today = date(2024, 3, 20)
    result = should_reset(last_reset, today)
    print(
        f"测试6: last_reset={last_reset}, today={today}, 应该重置={result} (期望: True)"
    )
    assert result == True, '测试6失败：应该重置'  # noqa: E712


if __name__ == '__main__':
    test_monthly_reset_logic()
