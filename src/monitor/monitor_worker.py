"""matmaster-monitor 入口（占位实现）。

供独立 Monitor Deployment 使用，与 API / Worker 共用同一代码库与镜像
（Dockerfile ``--target monitor``）。当前仅做两件事：

1. 周期打印心跳日志，证明进程存活、镜像与部署链路正常；
2. 响应 SIGTERM 优雅退出（在下一次心跳前置位退出标记），便于滚动发布。

后续接入真实监控逻辑时，在 ``_run_monitor_loop`` 内替换心跳为实际采集/巡检即可。
"""

import logging
import os
import signal
import threading

from src.utils.build_info import get_build_version
from src.utils.logger import LoggingConfig, setup_logging
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 心跳间隔（秒），可通过环境变量覆盖
_HEARTBEAT_INTERVAL = float(os.environ.get('MONITOR_HEARTBEAT_INTERVAL', '30'))

# 优雅退出标记：SIGTERM 时置位，主循环在下一次唤醒时退出
_stop_event = threading.Event()


def _run_monitor_loop() -> None:
    """占位主循环：周期打印心跳，直到收到退出信号。"""
    worker_id = get_worker_id()
    build_version = get_build_version()
    logger.info(
        'matmaster-monitor: started worker_id=%s build_version=%s interval=%.0fs',
        worker_id,
        build_version,
        _HEARTBEAT_INTERVAL,
    )

    tick = 0
    while not _stop_event.is_set():
        tick += 1
        logger.info(
            'matmaster-monitor: heartbeat tick=%d worker_id=%s', tick, worker_id
        )
        # wait 在收到 set() 时立即返回 True，可第一时间响应 SIGTERM
        _stop_event.wait(timeout=_HEARTBEAT_INTERVAL)

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
