"""matmaster-monitor 进程循环接入测试。

只验证「外壳 ↔ 巡检单元」的接缝：``_run_monitor_loop`` 每轮先后调用
``BohriumMonitor.tick()`` 与 ``BohriumCompletionScheduler.tick()``、各记一条
summary、收到 ``_stop_event`` 后干净退出。两个 tick 单元自身的行为（透传
summary / 吞异常）分别由 ``tests/services/test_bohrium_poller.py`` 与
``tests/services/test_bohrium_completion_scheduler.py`` 覆盖，这里不重复。
"""

from __future__ import annotations

import logging

from src.monitor import monitor_worker


def test_run_monitor_loop_ticks_poller_and_scheduler_each_round(monkeypatch, caplog):
    """循环每轮先 poller.tick 后 scheduler.tick，各记 summary，stop 后退出。"""
    ticks: list[str] = []

    class _StubMonitor:
        def tick(self) -> dict[str, int]:
            ticks.append("poll")
            # 一轮后请求退出，循环不再空转（真实进程靠 SIGTERM 置位）
            monitor_worker._stop_event.set()
            return {"claimed": 3, "polled": 2, "errors": 1}

    class _StubScheduler:
        def tick(self) -> dict[str, int]:
            ticks.append("delivery")
            return {"scanned": 5, "triggered": 1}

    monitor_worker._stop_event.clear()
    monkeypatch.setattr(monitor_worker, "BohriumMonitor", lambda: _StubMonitor())
    monkeypatch.setattr(
        monitor_worker, "BohriumCompletionScheduler", lambda: _StubScheduler()
    )
    monkeypatch.setattr(monitor_worker, "_TICK_INTERVAL", 0.0)

    try:
        with caplog.at_level(logging.INFO, logger="src.monitor.monitor_worker"):
            monitor_worker._run_monitor_loop()
    finally:
        monitor_worker._stop_event.clear()  # 复位模块级单例，避免污染后续测试

    # poller 设了 stop 之后本轮 scheduler 仍执行（先完成本轮再退出）
    assert ticks == ["poll", "delivery"]
    assert any(
        "bohrium" in r.getMessage() and "'claimed': 3" in r.getMessage()
        for r in caplog.records
    ), "应记录本轮 BohriumMonitor.tick() 返回的 summary"
    assert any(
        "delivery" in r.getMessage() and "'triggered': 1" in r.getMessage()
        for r in caplog.records
    ), "应记录本轮 BohriumCompletionScheduler.tick() 返回的 summary"
