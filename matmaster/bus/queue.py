"""Synchronous event bus backed by stdlib queue.Queue.

MessageBus is the core transport for BusEvent objects between
the agent kernel (producer) and QueueBridge (consumer).
"""

import queue

from matmaster.types.events import BusEvent


class MessageBus:
    """同步事件总线。

    Agent kernel 调用 emit() 发射 BusEvent，
    QueueBridge 调用 get() 消费事件。
    基于 queue.Queue，线程安全。
    单 producer（agent thread）单 consumer（bridge）模式。

    设计选择：同步 queue.Queue 而非 asyncio.Queue
    （agent 在 ThreadPoolExecutor 中同步运行）。
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue[BusEvent] = queue.Queue(maxsize=maxsize)

    def emit(self, event: BusEvent) -> None:
        """发射事件（线程安全）。"""
        self._queue.put(event)

    def get(self, timeout: float | None = None) -> BusEvent:
        """消费下一个事件（阻塞直到有事件或超时）。

        超时抛出 queue.Empty。
        """
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> BusEvent:
        """非阻塞消费。队列为空时抛出 queue.Empty。"""
        return self._queue.get_nowait()

    @property
    def pending(self) -> int:
        """待消费事件数量（近似值）。"""
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
