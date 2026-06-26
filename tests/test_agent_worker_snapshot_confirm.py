"""Worker 主循环的 delivery snapshot 时序（对所有 origin 的 run 生效）：

acquire → snapshot → run_agent(收到 snapshot) → [run 成功] confirm → release。
run 失败不 confirm；confirm 异常不阻断 release；snapshot 为 None 照常 run。
跑真实 _run_worker_loop 一轮（blpop 第二次返回 None + _drain_requested 退出）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.worker import agent_worker


def _run_one_round(
    monkeypatch,
    *,
    snapshot_obj,
    run_result,
    confirm_exc=None,
    run_agent_exc=None,
    origin=None,
    submit_confirmation_required=False,
    max_runtime_seconds=None,
    node_sku_id=None,
):
    """注入全部外部依赖，跑一轮循环，返回 (有序调用名列表, run_agent 收到的 kwargs)。"""
    calls: list[str] = []
    received: dict = {}

    payload = {
        "session_id": "sess-1",
        "task_id": "task-1",
        "user_prompt": "hi",
        # notify=False 跳过完成卡片/邮件分支，缩小注入面
        "delivery": {"notify": False},
        "origin": origin,
        "bohrium_submit_confirmation_required": submit_confirmation_required,
        "bohrium_job_max_runtime_seconds": max_runtime_seconds,
        "bohrium_node_sku_id": node_sku_id,
    }

    fake_redis = MagicMock()
    fake_redis.blpop_agent_run_job.side_effect = [payload, None]
    fake_redis.is_stop_requested.return_value = False  # 取消桥轮询不得触发 cancel
    fake_redis.llen_agent_run_queue.return_value = 0
    monkeypatch.setattr(agent_worker, "get_redis_dao", lambda: fake_redis)

    fake_sessions = MagicMock()
    fake_sessions.try_acquire_session_run.side_effect = lambda sid: (
        calls.append("acquire"),
        (True, None),
    )[1]
    fake_sessions.get_session_user_id.return_value = "u1"
    fake_sessions.release_session_run.side_effect = (
        lambda sid, run_success: calls.append(f"release:{run_success}")
    )
    monkeypatch.setattr(agent_worker, "get_sessions_service", lambda: fake_sessions)

    async def fake_run_agent(**kwargs):
        calls.append("run_agent")
        received.update(kwargs)
        agent_worker._drain_requested = True
        if run_agent_exc is not None:
            raise run_agent_exc
        return (run_result, 5, None)

    fake_ars = MagicMock()
    fake_ars.run_agent = fake_run_agent
    monkeypatch.setattr(agent_worker, "get_agent_run_service", lambda: fake_ars)

    def fake_snapshot(session_id, *, workspace=None):
        calls.append("snapshot")
        received["snapshot_workspace"] = workspace
        return snapshot_obj

    def fake_confirm(snap):
        calls.append("confirm")
        if confirm_exc is not None:
            raise confirm_exc
        return 1

    monkeypatch.setattr(agent_worker.bohrium_delivery_ack, "snapshot", fake_snapshot)
    monkeypatch.setattr(agent_worker.bohrium_delivery_ack, "confirm", fake_confirm)

    fake_user_service = MagicMock()
    fake_user_service.get_user_info_for_display.return_value = {
        "user_id": "u1",
        "nickname": "n",
        "email": "e",
    }
    monkeypatch.setattr(agent_worker, "UserService", fake_user_service)
    monkeypatch.setattr(agent_worker, "get_worker_registry_service", MagicMock())
    monkeypatch.setattr(agent_worker, "notify_post_async", lambda *a, **k: None)
    monkeypatch.setattr(agent_worker, "_drain_requested", False)

    agent_worker._run_worker_loop()
    return calls, received


def test_success_path_orders_snapshot_run_confirm_release(monkeypatch):
    snap = object()
    calls, received = _run_one_round(monkeypatch, snapshot_obj=snap, run_result=True)

    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]
    assert received["delivery_snapshot"] is snap
    assert received["snapshot_workspace"] is None
    assert received["job_context_mode"] == "workspace_observation"


def test_failed_run_skips_confirm(monkeypatch):
    calls, _ = _run_one_round(monkeypatch, snapshot_obj=object(), run_result=False)
    assert calls == ["acquire", "snapshot", "run_agent", "release:False"]


def test_run_agent_exception_skips_confirm_and_releases_failed(monkeypatch):
    calls, _ = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        run_agent_exc=RuntimeError("llm down"),
    )
    assert calls == ["acquire", "snapshot", "run_agent", "release:False"]


def test_confirm_failure_still_releases(monkeypatch):
    calls, _ = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        confirm_exc=RuntimeError("db down"),
    )
    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]


def test_none_snapshot_runs_without_confirm(monkeypatch):
    calls, received = _run_one_round(monkeypatch, snapshot_obj=None, run_result=True)
    assert calls == ["acquire", "snapshot", "run_agent", "release:True"]
    assert received["delivery_snapshot"] is None
    assert received["job_context_mode"] == "workspace_observation"


def test_bohrium_completion_origin_uses_delivery_mode(monkeypatch):
    snap = object()
    calls, received = _run_one_round(
        monkeypatch, snapshot_obj=snap, run_result=True, origin="bohrium_completion"
    )
    assert received["job_context_mode"] == "session_workspace_delivery"
    assert received["delivery_snapshot"] is snap
    assert calls == ["acquire", "snapshot", "run_agent", "confirm", "release:True"]


def test_submit_confirmation_flag_passed_to_run_agent(monkeypatch):
    _, received = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        submit_confirmation_required=True,
    )

    assert received["submit_confirmation_enabled"] is True


def test_bohrium_job_max_runtime_passed_to_run_agent(monkeypatch):
    _, received = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        max_runtime_seconds=7200,
    )

    assert received["bohrium_job_max_runtime_seconds"] == 7200


def test_bohrium_node_sku_id_passed_to_run_agent(monkeypatch):
    _, received = _run_one_round(
        monkeypatch,
        snapshot_obj=object(),
        run_result=True,
        node_sku_id="12345",
    )

    assert received["bohrium_node_sku_id"] == 12345
