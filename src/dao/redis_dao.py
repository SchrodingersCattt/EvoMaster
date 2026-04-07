"""Redis 连接与发布（DAO 层：仅负责与 Redis 的 I/O）。
另提供 confirmation_reply 多 worker 用：run_active 标记、run_context（task_id/invocation_id）、回复 list 的读写。
"""

import json
import logging
from functools import lru_cache
from typing import Any

import redis

from src.utils.constant import REDIS_URL

logger = logging.getLogger(__name__)

# confirmation_reply 多 worker：Redis key 与取消占位值
CONFIRMATION_RUN_ACTIVE_KEY = 'chat:run_active:{session_id}'
CONFIRMATION_RUN_CONTEXT_KEY = 'chat:run_context:{session_id}'
CONFIRMATION_REPLY_LIST_KEY = 'chat:confirmation_reply:{session_id}'
CONFIRMATION_CANCEL_VALUE = '__CANCEL__'
CONFIRMATION_RUN_ACTIVE_TTL_SEC = 3600

# 多 worker 时 run 所在 pod 向其它 pod 的 subscribe 流推送事件（Pub/Sub）
STREAM_CHANNEL_PREFIX = 'chat:stream:'

# agent run 队列（API 入队，Worker BLPOP）；用户停止标记（Worker 轮询）
AGENT_RUN_QUEUE_KEY = 'chat:agent_run_queue'
AGENT_STOP_KEY_PREFIX = 'chat:stop:'
AGENT_STOP_TTL_SEC = 3600

# 队列模式：API 入队后、Worker 接手前，subscribe 流用此标记保持打开，避免误判为「本进程在跑」导致流不关
SESSION_RUN_QUEUED_KEY_PREFIX = 'matmaster_chat:session_run_queued:'
SESSION_RUN_QUEUED_TTL_SEC = 300


def _run_active_key(session_id: str) -> str:
    return CONFIRMATION_RUN_ACTIVE_KEY.format(session_id=session_id.strip())


def _run_context_key(session_id: str) -> str:
    return CONFIRMATION_RUN_CONTEXT_KEY.format(session_id=session_id.strip())


def _reply_list_key(session_id: str) -> str:
    return CONFIRMATION_REPLY_LIST_KEY.format(session_id=session_id.strip())


def _stop_key(session_id: str, task_id: str) -> str:
    return AGENT_STOP_KEY_PREFIX + session_id.strip() + ':' + (task_id or '').strip()


def _session_run_queued_key(session_id: str) -> str:
    return SESSION_RUN_QUEUED_KEY_PREFIX + (session_id or '').strip()


class RedisDao:
    """Redis 访问：发布与创建连接。未配置 REDIS_URL 时各方法返回 None/False。"""

    def __init__(self) -> None:
        self._publish_client: Any | None = None

    def get_publish_client(self) -> Any | None:
        """进程内单例，用于 publish。"""
        if not REDIS_URL:
            return None
        if self._publish_client is None:
            self._publish_client = self._make_client()
        return self._publish_client

    def create_client(self) -> Any | None:
        """每次新建连接（供订阅线程等独立连接使用）。"""
        return self._make_client()

    def publish(self, channel: str, message: str) -> bool:
        """向指定 channel 发布一条消息。成功返回 True，未配置或失败返回 False。"""
        client = self.get_publish_client()
        if client is None:
            return False
        try:
            client.publish(channel, message)
            return True
        except Exception as e:
            logger.warning('Redis publish failed channel=%s: %s', channel, e)
            return False

    def publish_stream_event(self, session_id: str, payload: dict) -> bool:
        """向该会话的 stream channel 发布一条事件（多 worker 时供非执行 pod 的 subscribe 流消费）。"""
        channel = STREAM_CHANNEL_PREFIX + session_id.strip()
        try:
            message = json.dumps(payload, ensure_ascii=False)
            return self.publish(channel, message)
        except (TypeError, ValueError) as e:
            logger.warning(
                'Redis publish_stream_event json failed session_id=%s: %s',
                session_id,
                e,
            )
            return False

    @staticmethod
    def _make_client() -> Any | None:
        if not REDIS_URL:
            return None
        try:
            return redis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning('Redis client init failed: %s', e)
            return None

    # ---------- confirmation_reply 多 worker（run_active + reply list）----------

    def set_confirmation_run_active(self, session_id: str) -> bool:
        """标记该会话当前有活跃 run。未配置 Redis 或失败返回 False。"""
        client = self.create_client()
        if not client:
            return False
        try:
            client.set(
                _run_active_key(session_id),
                '1',
                ex=CONFIRMATION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                'Redis set run_active failed session_id=%s: %s', session_id, e
            )
            return False

    def delete_confirmation_run_active(self, session_id: str) -> None:
        """清除 run 活跃标记与 run_context。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.delete(_run_active_key(session_id))
            client.delete(_run_context_key(session_id))
        except Exception as e:
            logger.warning(
                'Redis delete run_active/run_context failed session_id=%s: %s',
                session_id,
                e,
            )

    def set_confirmation_run_context(
        self, session_id: str, task_id: str, invocation_id: str
    ) -> bool:
        """写入当前 run 的 task_id / invocation_id，供 broadcast_reply 跨 worker 使用。"""
        client = self.create_client()
        if not client:
            return False
        try:
            client.set(
                _run_context_key(session_id),
                json.dumps(
                    {'task_id': task_id, 'invocation_id': invocation_id},
                    ensure_ascii=False,
                ),
                ex=CONFIRMATION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                'Redis set run_context failed session_id=%s: %s', session_id, e
            )
            return False

    def get_confirmation_run_context(self, session_id: str) -> dict | None:
        """读取当前 run 的 task_id / invocation_id，无或失败返回 None。"""
        client = self.create_client()
        if not client:
            return None
        try:
            raw = client.get(_run_context_key(session_id))
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(
                'Redis get run_context failed session_id=%s: %s', session_id, e
            )
            return None

    def is_confirmation_run_active(self, session_id: str) -> bool:
        """是否配置了 Redis 且该会话在 Redis 中有活跃 run。"""
        client = self.create_client()
        if not client:
            return False
        try:
            return client.exists(_run_active_key(session_id)) > 0
        except Exception:
            return False

    def delete_confirmation_reply_list(self, session_id: str) -> None:
        """清空该会话的回复列表（新 run 开始时调用）。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.delete(_reply_list_key(session_id))
        except Exception as e:
            logger.warning(
                'Redis clear reply list failed session_id=%s: %s', session_id, e
            )

    def rpush_confirmation_reply(self, session_id: str, value: str) -> None:
        """向该会话回复列表尾部推入一条（内容或取消占位）。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.rpush(_reply_list_key(session_id), value)
        except Exception as e:
            logger.warning(
                'Redis RPUSH confirmation_reply failed session_id=%s: %s',
                session_id,
                e,
            )

    def blpop_confirmation_reply(self, session_id: str, timeout_sec: int) -> str | None:
        """从该会话回复列表左侧阻塞弹出一条。超时返回 None；否则返回字符串（可能为取消占位）。"""
        client = self.create_client()
        if not client:
            return None
        try:
            result = client.blpop(_reply_list_key(session_id), timeout=timeout_sec)
        except Exception as e:
            logger.warning(
                'Redis BLPOP confirmation_reply failed session_id=%s: %s',
                session_id,
                e,
            )
            return None
        if result is None:
            return None
        _, value = result
        return value

    # ---------- agent run 队列（API 入队，Worker BLPOP）----------

    def lpush_agent_run_job(self, payload: dict) -> bool:
        """将一次 run 任务入队。未配置 Redis 或失败返回 False。"""
        client = self.create_client()
        if not client:
            return False
        try:
            raw = json.dumps(payload, ensure_ascii=False)
            client.lpush(AGENT_RUN_QUEUE_KEY, raw)
            return True
        except Exception as e:
            logger.warning('Redis LPUSH agent_run_job failed: %s', e)
            return False

    def blpop_agent_run_job(self, timeout_sec: int = 30) -> dict | None:
        """阻塞取出一条 run 任务。超时返回 None；否则返回解析后的 dict。"""
        client = self.create_client()
        if not client:
            return None
        try:
            result = client.blpop(AGENT_RUN_QUEUE_KEY, timeout=timeout_sec)
        except Exception as e:
            logger.warning('Redis BLPOP agent_run_job failed: %s', e)
            return None
        if result is None:
            return None
        _, raw = result
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as e:
            logger.warning('Redis agent_run_job payload json load failed: %s', e)
            return None

    def llen_agent_run_queue(self) -> int:
        """当前 agent run 队列中等待的任务数（供飞书通知等使用）。未配置 Redis 或失败返回 0。"""
        client = self.create_client()
        if not client:
            return 0
        try:
            return int(client.llen(AGENT_RUN_QUEUE_KEY))  # type: ignore[no-any-return]
        except Exception as e:
            logger.warning('Redis LLEN agent_run_queue failed: %s', e)
            return 0

    def set_session_run_queued(self, session_id: str) -> bool:
        """队列模式：API 入队后设置，供 subscribe 流判断「任务已排队未接手」保持打开。Worker 接手时删除。"""
        client = self.create_client()
        if not client:
            return False
        try:
            client.set(
                _session_run_queued_key(session_id),
                '1',
                ex=SESSION_RUN_QUEUED_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                'Redis set_session_run_queued failed session_id=%s: %s',
                session_id,
                e,
            )
            return False

    def delete_session_run_queued(self, session_id: str) -> None:
        """Worker try_acquire 时删除，表示已接手。"""
        client = self.create_client()
        if not client:
            return
        try:
            client.delete(_session_run_queued_key(session_id))
        except Exception as e:
            logger.warning(
                'Redis delete_session_run_queued failed session_id=%s: %s',
                session_id,
                e,
            )

    def is_session_run_queued(self, session_id: str) -> bool:
        """该会话是否处于「已入队、Worker 尚未接手」状态。"""
        client = self.create_client()
        if not client:
            return False
        try:
            return client.exists(_session_run_queued_key(session_id)) > 0
        except Exception as e:
            logger.warning(
                'Redis is_session_run_queued failed session_id=%s: %s',
                session_id,
                e,
            )
            return False

    # ---------- 用户停止（Worker 轮询）----------

    def set_stop_requested(self, session_id: str, task_id: str) -> bool:
        """标记用户请求停止该 run。Worker 轮询 is_stop_requested。
        同时写入 session 级 key（task_id 为空），便于 ctx 尚未就绪时 stop 仍能生效。
        """
        client = self.create_client()
        if not client:
            return False
        try:
            key = _stop_key(session_id, task_id)
            client.set(key, '1', ex=AGENT_STOP_TTL_SEC)
            # 始终再写 session 级 key，供 Worker 在「仅按 session」时也能看到
            session_key = _stop_key(session_id, '')
            if session_key != key:
                client.set(session_key, '1', ex=AGENT_STOP_TTL_SEC)
            return True
        except Exception as e:
            logger.warning(
                'Redis set_stop_requested failed session_id=%s task_id=%s: %s',
                session_id,
                task_id,
                e,
            )
            return False

    def is_stop_requested(self, session_id: str, task_id: str) -> bool:
        """是否已请求停止该 run。同时检查 task 级 key 与 session 级 key（ctx 未就绪时仅写 session 级）。"""
        client = self.create_client()
        if not client:
            return False
        try:
            if client.exists(_stop_key(session_id, task_id)) > 0:
                return True
            if (task_id or '').strip():
                return client.exists(_stop_key(session_id, '')) > 0
            return False
        except Exception:
            return False

    def delete_stop_requested(self, session_id: str, task_id: str) -> None:
        """清除停止标记（run 结束后）。同时删除 task 级与 session 级 key。
        优先使用 get_publish_client（与 Worker publish 同连接），避免 create_client 在部分环境不可用导致未删。
        单次 delete 多 key，避免只删掉一个 key 而 session 级残留。
        """
        client = self.get_publish_client() or self.create_client()
        if not client:
            logger.warning(
                'Redis delete_stop_requested: no client (session_id=%s task_id=%s), skip',
                session_id,
                task_id or '(session-only)',
            )
            return
        key_task = _stop_key(session_id, task_id)
        key_session = _stop_key(session_id, '')
        try:
            deleted = client.delete(key_task, key_session)
            logger.info(
                'Redis delete_stop_requested: session_id=%s task_id=%s keys=%s,%s deleted=%s',
                session_id,
                task_id or '(session-only)',
                key_task,
                key_session,
                deleted,
            )
        except Exception as e:
            logger.warning(
                'Redis delete_stop_requested failed session_id=%s task_id=%s: %s',
                session_id,
                task_id,
                e,
            )


@lru_cache(maxsize=1)
def get_redis_dao() -> RedisDao:
    return RedisDao()
