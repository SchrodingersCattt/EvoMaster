"""Tests for worker completion-card / duration / email pure helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.utils.feishu_notifier import (
    CARD_TEMPLATE_GREEN,
    CARD_TEMPLATE_ORANGE,
    CARD_TEMPLATE_RED,
)
from src.worker.agent_worker import (
    _build_completion_card,
    _format_run_duration,
    _send_completion_email,
)


def _card(**overrides: object) -> tuple[str, list[tuple[str, str]], str]:
    kwargs: dict[str, object] = dict(
        session_id="s1",
        session_url="http://session",
        user_info_display="user",
        llm="llm-cfg",
        model="gpt",
        user_question="q",
        run_success=True,
        fail_reason=None,
        fail_reason_str="",
        duration_str="1.0 秒",
        active_count=2,
        queue_len=3,
        usage_summary=None,
    )
    kwargs.update(overrides)
    return _build_completion_card(**kwargs)  # type: ignore[arg-type]


# -- _format_run_duration --


def test_duration_seconds() -> None:
    assert _format_run_duration(12.34) == "12.3 秒"
    assert _format_run_duration(59.9) == "59.9 秒"


def test_duration_minutes() -> None:
    assert _format_run_duration(60) == "1 分 0 秒"
    assert _format_run_duration(125) == "2 分 5 秒"


def test_duration_hours() -> None:
    assert _format_run_duration(3725) == "1 小时 2 分"


# -- _build_completion_card --


def test_card_success() -> None:
    title, rows, template = _card(run_success=True)
    assert title == "Worker 执行成功"
    assert template == CARD_TEMPLATE_GREEN
    labels = [k for k, _ in rows]
    assert labels[:5] == ["会话ID", "会话地址", "用户", "模型", "用户问题"]
    assert "失败原因" not in labels
    assert ("运行时间", "1.0 秒") in rows
    assert ("结果", "成功") in rows


def test_card_failed_inserts_reason_after_result() -> None:
    title, rows, template = _card(
        run_success=False, fail_reason="boom", fail_reason_str="boom"
    )
    assert title == "Worker 执行失败"
    assert template == CARD_TEMPLATE_RED
    assert rows[6] == ("结果", "失败")
    assert rows[7] == ("失败原因", "boom")


def test_card_failed_reason_truncated() -> None:
    long_reason = "x" * 600
    _title, rows, _template = _card(
        run_success=False, fail_reason="oops", fail_reason_str=long_reason
    )
    reason_val = dict(rows)["失败原因"]
    assert reason_val.endswith("…")
    assert len(reason_val) == 501  # 500 chars + ellipsis


def test_card_cancelled() -> None:
    title, rows, template = _card(
        run_success=False, fail_reason="cancelled", fail_reason_str="cancelled"
    )
    assert title == "用户取消运行"
    assert template == CARD_TEMPLATE_ORANGE
    labels = [k for k, _ in rows]
    assert "失败原因" not in labels
    assert ("结果", "已取消") in rows


def test_card_quota_exhausted_is_failure_with_friendly_reason() -> None:
    # 主循环把 quota_exhausted 经 _FAIL_REASON_DISPLAY 映射成友好文案后传入；
    # 成本熔断按「失败」（红卡）而非「用户取消」（橙卡）呈现。
    from src.worker.agent_worker import _FAIL_REASON_DISPLAY

    display = _FAIL_REASON_DISPLAY["quota_exhausted"]
    assert display == "额度已用完，本轮已自动停止"
    title, rows, template = _card(
        run_success=False, fail_reason="quota_exhausted", fail_reason_str=display
    )
    assert title == "Worker 执行失败"
    assert template == CARD_TEMPLATE_RED
    assert rows[6] == ("结果", "失败")
    assert rows[7] == ("失败原因", display)


def test_card_inserts_usage_rows_after_runtime() -> None:
    summary = {
        "num_turns": 2,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }
    _title, rows, _template = _card(run_success=True, usage_summary=summary)
    labels = [k for k, _ in rows]
    idx = labels.index("运行时间")
    assert labels[idx + 1] == "Token 消耗"
    assert labels[idx + 2] == "LLM 轮数"


# -- _send_completion_email --


def _email(**overrides: object) -> None:
    kwargs: dict[str, object] = dict(
        session_user_id="u1",
        user_info={"email": "a@b.com"},
        payload={},
        session_url="http://session",
        user_question="q",
        duration_str="1.0 秒",
        run_success=True,
        fail_reason=None,
        fail_reason_str="",
    )
    kwargs.update(overrides)
    _send_completion_email(**kwargs)  # type: ignore[arg-type]


def test_email_skipped_without_address() -> None:
    with patch("src.worker.agent_worker.send_session_complete_email_async") as m:
        _email(user_info={"email": "-"})
        _email(user_info={})
        m.assert_not_called()


def test_email_skipped_without_user_id() -> None:
    with patch("src.worker.agent_worker.send_session_complete_email_async") as m:
        _email(session_user_id=None)
        m.assert_not_called()


def test_email_sent_on_success() -> None:
    with patch("src.worker.agent_worker.send_session_complete_email_async") as m:
        _email(run_success=True)
        m.assert_called_once()
        args, kwargs = m.call_args
        assert args[0] == "http://session"
        assert args[1] == "u1"
        assert args[2] == "a@b.com"
        assert kwargs["result_status"] == "成功"
        assert kwargs["fail_reason"] == ""


def test_email_failure_passes_reason() -> None:
    with patch("src.worker.agent_worker.send_session_complete_email_async") as m:
        _email(run_success=False, fail_reason="boom", fail_reason_str="boom")
        _args, kwargs = m.call_args
        assert kwargs["result_status"] == "失败"
        assert kwargs["fail_reason"] == "boom"


def test_email_cancelled_clears_reason() -> None:
    with patch("src.worker.agent_worker.send_session_complete_email_async") as m:
        _email(run_success=False, fail_reason="cancelled", fail_reason_str="cancelled")
        _args, kwargs = m.call_args
        assert kwargs["result_status"] == "已取消"
        assert kwargs["fail_reason"] == ""
