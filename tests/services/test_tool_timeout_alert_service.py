from __future__ import annotations

from matmaster.types.runtime_ports import ToolTimeoutNotice
from src.services import tool_timeout_alert_service as svc
from src.utils.feishu_notifier import CARD_TEMPLATE_RED


def _notice(tool_name: str = "mat_struct_db_fetch_structures_from_db"):
    return ToolTimeoutNotice(
        session_id="sess-1",
        task_id="task-1",
        spawn_id=None,
        tool_name=tool_name,
        tool_call_id="call-1",
        turn=3,
        result_content="MCP tool timed out after 30s",
        arguments_preview='{"formula": "SiO2"}',
    )


class _Redis:
    def __init__(self, reserved):
        self.reserved = reserved
        self.calls = []

    def try_reserve_nx(self, key, value, ttl_sec):
        self.calls.append((key, value, ttl_sec))
        return self.reserved


def test_non_critical_tool_timeout_is_ignored(monkeypatch):
    sent = []
    redis = _Redis(True)
    monkeypatch.setattr(svc, "get_redis_dao", lambda: redis)
    monkeypatch.setattr(
        svc, "notify_post_async", lambda *args, **kwargs: sent.append(args)
    )

    svc.FeishuToolTimeoutObserver()(_notice("web_fetch"))

    assert sent == []
    assert redis.calls == []


def test_dedup_hit_suppresses_alert(monkeypatch):
    sent = []
    redis = _Redis(False)
    monkeypatch.setattr(svc, "get_redis_dao", lambda: redis)
    monkeypatch.setattr(
        svc, "notify_post_async", lambda *args, **kwargs: sent.append(args)
    )

    svc.FeishuToolTimeoutObserver()(_notice())

    assert sent == []
    assert redis.calls


def test_critical_tool_timeout_sends_feishu_card(monkeypatch):
    sent = []
    redis = _Redis(True)
    monkeypatch.setattr(svc, "get_redis_dao", lambda: redis)
    monkeypatch.setattr(svc, "get_worker_id", lambda: "worker-1")
    monkeypatch.setattr(
        svc,
        "notify_post_async",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )

    svc.FeishuToolTimeoutObserver()(_notice())

    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[0] == "核心 MCP 工具超时"
    assert kwargs["template"] == CARD_TEMPLATE_RED
    rows = dict(args[1])
    assert rows["会话ID"] == "sess-1"
    assert rows["任务ID"] == "task-1"
    assert rows["执行节点"] == "worker-1"
    assert rows["工具"] == "mat_struct_db_fetch_structures_from_db"
