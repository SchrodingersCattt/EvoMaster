"""matmaster-monitor 入口：独立 Monitor Deployment 的进程外壳。

与 API / Worker 共用同一代码库与镜像（Dockerfile ``--target monitor``）。进程做三件事：

1. 每轮调 ``BohriumMonitor.tick()`` 推进活跃 Bohrium 作业到终态（claim 到期作业、
   查平台、写回 ledger）；每轮 summary 日志同时充当进程存活证明；
2. 响应 SIGTERM 优雅退出（下一轮唤醒前置位退出标记），便于滚动发布；
3. 单轮巡检异常由 ``BohriumMonitor.tick()`` 自吞，进程绝不因单轮失败退出。
"""

import logging
import os
import signal
import threading

from src.services.bohrium_poller import BohriumMonitor
from src.utils.build_info import get_build_version
from src.utils.logger import LoggingConfig, setup_logging
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 巡检间隔（秒）：进程多久 claim 一次到期作业，可通过环境变量覆盖。
# 注意这 ≠ 单作业 poll 频率（后者由 ledger 的 next_poll_at 退避决定）。
_TICK_INTERVAL = float(os.environ.get('MONITOR_TICK_INTERVAL', '10'))

# 优雅退出标记：SIGTERM 时置位，主循环在下一次唤醒时退出
_stop_event = threading.Event()


def _run_monitor_loop() -> None:
    """主循环：每轮巡检一次 Bohrium 作业，直到收到退出信号。"""
    worker_id = get_worker_id()
    build_version = get_build_version()
    logger.info(
        'matmaster-monitor: started worker_id=%s build_version=%s interval=%.0fs',
        worker_id,
        build_version,
        _TICK_INTERVAL,
    )

    runner = BohriumMonitor()  # 循环外构造一次（惰性、无 DB、tick 不抛异常）
    while not _stop_event.is_set():
        summary = runner.tick()  # 单轮 claim + poll + 写回 ledger
        logger.info('matmaster-monitor: bohrium %s worker_id=%s', summary, worker_id)
        # wait 在收到 set() 时立即返回 True，可第一时间响应 SIGTERM
        _stop_event.wait(timeout=_TICK_INTERVAL)

    logger.info('matmaster-monitor: loop exited worker_id=%s', worker_id)


def main() -> None:
    setup_logging(**LoggingConfig.get_monitor_config())

    def _on_sigterm(_signum: int, _frame: object) -> None:
        logger.info(
            'matmaster-monitor: received SIGTERM, will exit after current tick. worker_id=%s',
            get_worker_id(),
        )
        _stop_event.set()

    signal.signal(signal.SIGTERM, _on_sigterm)

    _run_monitor_loop()


if __name__ == '__main__':
    main()
