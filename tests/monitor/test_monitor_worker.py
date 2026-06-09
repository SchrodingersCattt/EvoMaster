"""matmaster-monitor 进程循环接入测试。

只验证「外壳 ↔ 巡检单元」的接缝：``_run_monitor_loop`` 每轮构造并调用
``BohriumMonitor.tick()``、记录其 summary、收到 ``_stop_event`` 后干净退出。
巡检单元自身的行为（透传 summary / 吞异常）由
``tests/services/test_bohrium_poller.py`` 覆盖，这里不重复。
"""

from __future__ import annotations

import logging

from src.monitor import monitor_worker


def test_run_monitor_loop_ticks_each_round_and_exits_on_stop(monkeypatch, caplog):
    """循环每轮调 BohriumMonitor.tick() 并记 summary，置位 stop_event 后退出。"""
    ticks: list[int] = []

    class _StubMonitor:
        def tick(self) -> dict[str, int]:
            ticks.append(1)
            # 一轮后请求退出，循环不再空转（真实进程靠 SIGTERM 置位）
            monitor_worker._stop_event.set()
            return {"claimed": 3, "polled": 2, "errors": 1}

    monitor_worker._stop_event.clear()
    monkeypatch.setattr(monitor_worker, "BohriumMonitor", lambda: _StubMonitor())
    monkeypatch.setattr(monitor_worker, "_TICK_INTERVAL", 0.0)

    try:
        with caplog.at_level(logging.INFO, logger="src.monitor.monitor_worker"):
            monitor_worker._run_monitor_loop()
    finally:
        monitor_worker._stop_event.clear()  # 复位模块级单例，避免污染后续测试

    assert ticks == [1]
    assert any(
        "bohrium" in r.getMessage() and "'claimed': 3" in r.getMessage()
        for r in caplog.records
    ), "应记录本轮 BohriumMonitor.tick() 返回的 summary"
