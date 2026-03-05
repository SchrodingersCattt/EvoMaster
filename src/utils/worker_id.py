"""当前进程的 worker 标识，用于多 worker 下 session run 归属与存活判断。"""

import os
import socket


def get_worker_id() -> str:
    """返回当前进程的 worker 标识（hostname:pid），每次调用现算，无缓存。"""
    return f"{socket.gethostname()}:{os.getpid()}"
