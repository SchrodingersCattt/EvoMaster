"""Chat stream service 内部数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunHandle:
    """_prepare_run 的成功产物：已写好发起事件、已组好 job，待 _enqueue_run 入队。"""

    task_id: str
    invocation_id: str
    job: dict
    event: dict  # 已落库的发起事件（User/query 或 System/trigger）


@dataclass
class Busy:
    """_prepare_run 因会话运行锁被占而放弃的产物。"""

    reason: str  # already_in_run | db_update_failed | unknown


@dataclass
class TriggerResult:
    """trigger_run 的返回。status: enqueued | deduped | busy | error。"""

    status: str
    task_id: str | None = None
    invocation_id: str | None = None
    dedup_key: str | None = None
    reason: str | None = None


@dataclass
class TriggerStreamContext:
    """内部 trigger 已写好发起事件、已组好 job，待订阅就绪后入队。"""

    task_id: str
    invocation_id: str
    owner: str
    job: dict
    event: dict  # 已落库的 System/trigger 发起事件
    dedup_key: str | None = None


@dataclass
class SendStreamContext:
    """发送消息流所需上下文，由 prepare_send_message 返回。"""

    task_id: str
    invocation_id: str  # 本轮调用的唯一标识，前端用于区分第几轮
    mode: str
    user_msg: dict
    job: dict  # _prepare_run 组好的入队 job；由 generate_send_stream 经 _enqueue_run 入队
