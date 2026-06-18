"""Redis 连接与发布（DAO 层：仅负责与 Redis 的 I/O）。
另提供 interaction 多 worker 用：run_active 标记、run_context 与 per-request
交互传输原语。
"""

import json
import logging
from functools import lru_cache
from typing import Any

import redis

from src.utils.constant import REDIS_URL

logger = logging.getLogger(__name__)

# 命令客户端连接 stale 时透明重连：redis-py 在连接空闲超过该秒数后、复用前先 PING 校验
REDIS_HEALTH_CHECK_INTERVAL_SEC = 30

# interaction reply 多 worker：Redis key 与取消占位值
INTERACTION_RUN_ACTIVE_KEY = "chat:run_active:{session_id}"
INTERACTION_RUN_CONTEXT_KEY = "chat:run_context:{session_id}"
INTERACTION_CANCEL_VALUE = "__CANCEL__"
INTERACTION_RUN_ACTIVE_TTL_SEC = 3600
HUMAN_INTERACTION_KEY = "human_interaction:{request_id}"
INTERACTION_REPLY_KEY = "interaction_reply:{request_id}"
HUMAN_INTERACTION_ACTIVE_KEY = "human_interaction_active:{session_id}"
INTERACTION_TERMINAL_TTL = 300
INTERACTION_REPLY_BUFFER = 60

# 多 worker 时 run 所在 pod 向其它 pod 的 subscribe 流推送事件（Pub/Sub）
STREAM_CHANNEL_PREFIX = "chat:stream:"
USER_WAKEUP_CHANNEL_PREFIX = "chat:user:"

# agent run 队列（API 入队，Worker BLPOP）；用户停止标记（Worker 轮询）
AGENT_RUN_QUEUE_KEY = "chat:agent_run_queue"
AGENT_STOP_KEY_PREFIX = "chat:stop:"
AGENT_STOP_TTL_SEC = 3600
DEDUP_KEY_PREFIX = "chat:trigger:dedup:"
DEFAULT_DEDUP_TTL_SEC = 86400  # 24h，程序化触发幂等窗口默认值

# 队列模式：API 入队后、Worker 接手前，subscribe 流用此标记保持打开，避免误判为「本进程在跑」导致流不关
SESSION_RUN_QUEUED_KEY_PREFIX = "matmaster_chat:session_run_queued:"
SESSION_RUN_QUEUED_TTL_SEC = 300

# 用户排队中断：前端有排队消息时设置 hint，Worker checkpoint 处检查
INTERRUPT_HINT_KEY_PREFIX = "chat:interrupt_hint:"
INTERRUPT_HINT_TTL_SEC = 60
INTERRUPT_CONFIRM_KEY_PREFIX = "chat:interrupt_confirm:"
INTERRUPT_CONFIRM_TTL_SEC = 10


def _run_active_key(session_id: str) -> str:
    return INTERACTION_RUN_ACTIVE_KEY.format(session_id=session_id.strip())


def _run_context_key(session_id: str) -> str:
    return INTERACTION_RUN_CONTEXT_KEY.format(session_id=session_id.strip())


def _stop_key(session_id: str, task_id: str) -> str:
    return AGENT_STOP_KEY_PREFIX + session_id.strip() + ":" + (task_id or "").strip()


def _human_interaction_key(request_id: str) -> str:
    return HUMAN_INTERACTION_KEY.format(request_id=request_id)


def _interaction_reply_key(request_id: str) -> str:
    return INTERACTION_REPLY_KEY.format(request_id=request_id)


def _human_interaction_active_key(session_id: str) -> str:
    return HUMAN_INTERACTION_ACTIVE_KEY.format(session_id=session_id)


def user_wakeup_channel(user_id: str) -> str:
    return USER_WAKEUP_CHANNEL_PREFIX + (user_id or "").strip() + ":wakeup"


def _session_run_queued_key(session_id: str) -> str:
    return SESSION_RUN_QUEUED_KEY_PREFIX + (session_id or "").strip()


def _interrupt_hint_key(session_id: str) -> str:
    return INTERRUPT_HINT_KEY_PREFIX + (session_id or "").strip()


def _interrupt_confirm_key(session_id: str) -> str:
    return INTERRUPT_CONFIRM_KEY_PREFIX + (session_id or "").strip()


_ANSWER_PENDING_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
if redis.call('HGET', KEYS[1], 'state') ~= 'pending' then
  return 1
end
redis.call('HSET', KEYS[1], 'state', 'answered')
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 2
"""

_FINALIZE_INTERACTION_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
if redis.call('HGET', KEYS[1], 'state') ~= 'pending' then
  return 0
end
redis.call('HSET', KEYS[1], 'state', ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

_RELEASE_ACTIVE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisDao:
    """Redis 访问：发布与创建连接。未配置 REDIS_URL 时各方法返回 None/False。"""

    def __init__(self) -> None:
        self._publish_client: Any | None = None
        self._command_client: Any | None = None

    def get_publish_client(self) -> Any | None:
        """进程内单例，用于 publish。"""
        if not REDIS_URL:
            return None
        if self._publish_client is None:
            self._publish_client = self._make_client()
        return self._publish_client

    def get_command_client(self) -> Any | None:
        """进程内单例，供非阻塞一次性命令复用（set/get/exists/lpush/llen 等）。

        复用 redis-py 连接池：池线程安全、断连后自动取新连接重建，配合 _make_client
        的 health_check_interval 在连接 stale 时复用前透明重连。订阅线程与阻塞 BLPOP
        仍用 create_client 取独立连接。
        """
        if not REDIS_URL:
            return None
        if self._command_client is None:
            self._command_client = self._make_client()
        return self._command_client

    def create_client(self) -> Any | None:
        """每次新建连接（供订阅线程、阻塞 BLPOP 等需独立连接的场景使用）。"""
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
            logger.warning("Redis publish failed channel=%s: %s", channel, e)
            return False

    def publish_stream_event(self, session_id: str, payload: dict) -> bool:
        """向该会话的 stream channel 发布一条事件（多 worker 时供非执行 pod 的 subscribe 流消费）。"""
        channel = STREAM_CHANNEL_PREFIX + session_id.strip()
        try:
            message = json.dumps(payload, ensure_ascii=False)
            return self.publish(channel, message)
        except (TypeError, ValueError) as e:
            logger.warning(
                "Redis publish_stream_event json failed session_id=%s: %s",
                session_id,
                e,
            )
            return False

    def publish_user_wakeup(self, user_id: str, payload: dict) -> bool:
        """向该用户的 wakeup channel 发布一条 session 唤醒信号。"""
        channel = user_wakeup_channel(user_id)
        try:
            message = json.dumps(payload, ensure_ascii=False)
            return self.publish(channel, message)
        except (TypeError, ValueError) as e:
            logger.warning(
                "Redis publish_user_wakeup json failed user_id=%s: %s", user_id, e
            )
            return False

    @staticmethod
    def _make_client() -> Any | None:
        if not REDIS_URL:
            return None
        try:
            return redis.from_url(
                REDIS_URL,
                decode_responses=True,
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SEC,
            )
        except Exception as e:
            logger.warning("Redis client init failed: %s", e)
            return None

    # ---------- interaction 多 worker（run_active + per-request reply）----------

    def set_interaction_run_active(self, session_id: str) -> bool:
        """标记该会话当前有活跃 run。未配置 Redis 或失败返回 False。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            client.set(
                _run_active_key(session_id),
                "1",
                ex=INTERACTION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                "Redis set run_active failed session_id=%s: %s", session_id, e
            )
            return False

    def delete_interaction_run_active(self, session_id: str) -> None:
        """清除 run 活跃标记与 run_context。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(_run_active_key(session_id))
            client.delete(_run_context_key(session_id))
        except Exception as e:
            logger.warning(
                "Redis delete run_active/run_context failed session_id=%s: %s",
                session_id,
                e,
            )

    def set_interaction_run_context(
        self, session_id: str, task_id: str, invocation_id: str
    ) -> bool:
        """写入当前 run 的 task_id / invocation_id，供 broadcast_reply 跨 worker 使用。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            client.set(
                _run_context_key(session_id),
                json.dumps(
                    {"task_id": task_id, "invocation_id": invocation_id},
                    ensure_ascii=False,
                ),
                ex=INTERACTION_RUN_ACTIVE_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                "Redis set run_context failed session_id=%s: %s", session_id, e
            )
            return False

    def get_interaction_run_context(self, session_id: str) -> dict | None:
        """读取当前 run 的 task_id / invocation_id，无或失败返回 None。"""
        client = self.get_command_client()
        if not client:
            return None
        try:
            raw = client.get(_run_context_key(session_id))
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning(
                "Redis get run_context failed session_id=%s: %s", session_id, e
            )
            return None

    def is_interaction_run_active(self, session_id: str) -> bool:
        """是否配置了 Redis 且该会话在 Redis 中有活跃 run。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            return client.exists(_run_active_key(session_id)) > 0
        except Exception:
            return False

    def write_pending_interaction(
        self, request_id: str, record: dict, ttl: int
    ) -> None:
        """写 pending registry（hash + TTL）。"""
        client = self.get_command_client()
        if not client:
            return
        key = _human_interaction_key(request_id)
        try:
            client.hset(
                key,
                mapping={k: ("" if v is None else str(v)) for k, v in record.items()},
            )
            client.expire(key, ttl)
        except Exception as e:
            logger.warning("write_pending_interaction failed: %s", e)

    def read_pending_interaction(self, request_id: str) -> dict | None:
        """读 pending registry；不存在返回 None。"""
        client = self.get_command_client()
        if not client:
            return None
        try:
            data = client.hgetall(_human_interaction_key(request_id))
        except Exception as e:
            logger.warning("read_pending_interaction failed: %s", e)
            return None
        if not data:
            return None
        return {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in data.items()
        }

    def blpop_interaction_reply(self, request_id: str, timeout_sec: int) -> str | None:
        """阻塞等待 per-request reply key；超时返回 None。"""
        client = self.create_client()
        if not client:
            return None
        try:
            result = client.blpop(
                _interaction_reply_key(request_id), timeout=timeout_sec
            )
        except Exception as e:
            logger.warning("blpop_interaction_reply failed: %s", e)
            return None
        if result is None:
            return None
        _, value = result
        return value.decode() if isinstance(value, bytes) else value

    def rpush_interaction_cancel(self, request_id: str) -> None:
        """向 per-request reply key 投取消哨兵，唤醒 BLPOP。"""
        client = self.get_command_client()
        if not client:
            return
        key = _interaction_reply_key(request_id)
        try:
            client.rpush(key, INTERACTION_CANCEL_VALUE)
            client.expire(key, INTERACTION_REPLY_BUFFER)
        except Exception as e:
            logger.warning("rpush_interaction_cancel failed: %s", e)

    def delete_interaction_reply(self, request_id: str) -> None:
        """cleanup per-request reply key（worker 正常结束路径）。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(_interaction_reply_key(request_id))
        except Exception as e:
            logger.warning("delete_interaction_reply failed: %s", e)

    def answer_pending_interaction(self, request_id: str, envelope: str) -> str:
        """原子校验 pending、写 answered 并 RPUSH reply envelope。"""
        client = self.get_command_client()
        if not client:
            return "not_found"
        try:
            code = client.eval(
                _ANSWER_PENDING_LUA,
                2,
                _human_interaction_key(request_id),
                _interaction_reply_key(request_id),
                envelope,
                str(INTERACTION_TERMINAL_TTL),
                str(INTERACTION_REPLY_BUFFER),
            )
        except Exception as e:
            logger.warning("answer_pending_interaction failed: %s", e)
            return "not_found"
        return {0: "not_found", 1: "not_pending", 2: "ok"}.get(int(code), "not_found")

    def finalize_interaction(self, request_id: str, state: str) -> bool:
        """仅当 pending 时改 terminal=state；幂等返回本次是否改成。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            changed = client.eval(
                _FINALIZE_INTERACTION_LUA,
                1,
                _human_interaction_key(request_id),
                state,
                str(INTERACTION_TERMINAL_TTL),
            )
        except Exception as e:
            logger.warning("finalize_interaction failed: %s", e)
            return False
        return int(changed) == 1

    def acquire_active_interaction(self, session_id: str, request_id: str) -> bool:
        """SETNX active 守卫；占用中返回 False。TTL 兜底防泄漏。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            ok = client.set(
                _human_interaction_active_key(session_id),
                request_id,
                nx=True,
                ex=3600,
            )
        except Exception as e:
            logger.warning("acquire_active_interaction failed: %s", e)
            return False
        return bool(ok)

    def release_active_interaction(self, session_id: str, request_id: str) -> None:
        """compare-and-delete：仅当当前 active==request_id 时释放。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.eval(
                _RELEASE_ACTIVE_LUA,
                1,
                _human_interaction_active_key(session_id),
                request_id,
            )
        except Exception as e:
            logger.warning("release_active_interaction failed: %s", e)

    def get_active_interaction(self, session_id: str) -> str | None:
        """取当前 active request_id（供 stop 定位）；无则 None。"""
        client = self.get_command_client()
        if not client:
            return None
        try:
            value = client.get(_human_interaction_active_key(session_id))
        except Exception as e:
            logger.warning("get_active_interaction failed: %s", e)
            return None
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    # ---------- agent run 队列（API 入队，Worker BLPOP）----------

    def lpush_agent_run_job(self, payload: dict) -> bool:
        """将一次 run 任务入队。未配置 Redis 或失败返回 False。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            raw = json.dumps(payload, ensure_ascii=False)
            client.lpush(AGENT_RUN_QUEUE_KEY, raw)
            return True
        except Exception as e:
            logger.warning("Redis LPUSH agent_run_job failed: %s", e)
            return False

    def blpop_agent_run_job(self, timeout_sec: int = 30) -> dict | None:
        """阻塞取出一条 run 任务。超时返回 None；否则返回解析后的 dict。"""
        client = self.create_client()
        if not client:
            return None
        try:
            result = client.blpop(AGENT_RUN_QUEUE_KEY, timeout=timeout_sec)
        except Exception as e:
            logger.warning("Redis BLPOP agent_run_job failed: %s", e)
            return None
        if result is None:
            return None
        _, raw = result
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as e:
            logger.warning("Redis agent_run_job payload json load failed: %s", e)
            return None

    def llen_agent_run_queue(self) -> int:
        """当前 agent run 队列中等待的任务数（供飞书通知等使用）。未配置 Redis 或失败返回 0。"""
        client = self.get_command_client()
        if not client:
            return 0
        try:
            return int(client.llen(AGENT_RUN_QUEUE_KEY))  # type: ignore[no-any-return]
        except Exception as e:
            logger.warning("Redis LLEN agent_run_queue failed: %s", e)
            return 0

    def set_session_run_queued(self, session_id: str) -> bool:
        """队列模式：API 入队后设置，供 subscribe 流判断「任务已排队未接手」保持打开。Worker 接手时删除。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            client.set(
                _session_run_queued_key(session_id),
                "1",
                ex=SESSION_RUN_QUEUED_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                "Redis set_session_run_queued failed session_id=%s: %s",
                session_id,
                e,
            )
            return False

    def dedup_key_exists(self, dedup_key: str) -> bool:
        """预检：dedup_key 是否已标记。无 Redis 或异常时按未命中处理。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            return bool(client.exists(DEDUP_KEY_PREFIX + dedup_key))
        except Exception as e:
            logger.warning("Redis dedup_key_exists failed key=%s: %s", dedup_key, e)
            return False

    def mark_dedup_key_nx(
        self, dedup_key: str, value: str, ttl_sec: int = DEFAULT_DEDUP_TTL_SEC
    ) -> bool:
        """成功入队后标记 dedup_key（SET NX EX）。返回是否首次设置成功。

        不区分「已被占位」与「Redis 不可用」，两者都按未标记成功处理。
        """
        return bool(self.try_reserve_nx(DEDUP_KEY_PREFIX + dedup_key, value, ttl_sec))

    def try_reserve_nx(self, key: str, value: str, ttl_sec: int) -> bool | None:
        """三态 SET NX EX 占位：True=占位成功 / False=已被占位 / None=无 client 或异常。

        与 mark_dedup_key_nx 的区别：调用方需要区分「已被占位」与「Redis 不可用」
        （后者按 fail-closed skip 并计数告警）。key 由调用方自带前缀，不加 DEDUP_KEY_PREFIX。
        """
        client = self.get_command_client()
        if not client:
            return None
        try:
            result = client.set(key, value, nx=True, ex=int(ttl_sec))
        except Exception as e:
            logger.warning("Redis try_reserve_nx failed key=%s: %s", key, e)
            return None
        return bool(result)

    def delete_session_run_queued(self, session_id: str) -> None:
        """Worker try_acquire 时删除，表示已接手。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(_session_run_queued_key(session_id))
        except Exception as e:
            logger.warning(
                "Redis delete_session_run_queued failed session_id=%s: %s",
                session_id,
                e,
            )

    def is_session_run_queued(self, session_id: str) -> bool:
        """该会话是否处于「已入队、Worker 尚未接手」状态。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            return client.exists(_session_run_queued_key(session_id)) > 0
        except Exception as e:
            logger.warning(
                "Redis is_session_run_queued failed session_id=%s: %s",
                session_id,
                e,
            )
            return False

    # ---------- 用户停止（Worker 轮询）----------

    def set_stop_requested(self, session_id: str, task_id: str) -> bool:
        """标记用户请求停止该 run。Worker 轮询 is_stop_requested。
        同时写入 session 级 key（task_id 为空），便于 ctx 尚未就绪时 stop 仍能生效。
        """
        client = self.get_command_client()
        if not client:
            return False
        try:
            key = _stop_key(session_id, task_id)
            client.set(key, "1", ex=AGENT_STOP_TTL_SEC)
            # 始终再写 session 级 key，供 Worker 在「仅按 session」时也能看到
            session_key = _stop_key(session_id, "")
            if session_key != key:
                client.set(session_key, "1", ex=AGENT_STOP_TTL_SEC)
            return True
        except Exception as e:
            logger.warning(
                "Redis set_stop_requested failed session_id=%s task_id=%s: %s",
                session_id,
                task_id,
                e,
            )
            return False

    def is_stop_requested(self, session_id: str, task_id: str) -> bool:
        """是否已请求停止该 run。同时检查 task 级 key 与 session 级 key（ctx 未就绪时仅写 session 级）。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            if client.exists(_stop_key(session_id, task_id)) > 0:
                return True
            if (task_id or "").strip():
                return client.exists(_stop_key(session_id, "")) > 0
            return False
        except Exception:
            return False

    def delete_stop_requested(self, session_id: str, task_id: str) -> None:
        """清除停止标记（run 结束后）。同时删除 task 级与 session 级 key。
        优先使用 get_publish_client（与 Worker publish 同连接），回退到复用的命令连接，避免每次新建连接。
        单次 delete 多 key，避免只删掉一个 key 而 session 级残留。
        """
        client = self.get_publish_client() or self.get_command_client()
        if not client:
            logger.warning(
                "Redis delete_stop_requested: no client (session_id=%s task_id=%s), skip",
                session_id,
                task_id or "(session-only)",
            )
            return
        key_task = _stop_key(session_id, task_id)
        key_session = _stop_key(session_id, "")
        try:
            deleted = client.delete(key_task, key_session)
            logger.info(
                "Redis delete_stop_requested: session_id=%s task_id=%s keys=%s,%s deleted=%s",
                session_id,
                task_id or "(session-only)",
                key_task,
                key_session,
                deleted,
            )
        except Exception as e:
            logger.warning(
                "Redis delete_stop_requested failed session_id=%s task_id=%s: %s",
                session_id,
                task_id,
                e,
            )

    # ---------- 用户排队中断（interrupt hint / confirm）----------

    def set_interrupt_hint(self, session_id: str) -> bool:
        """前端有排队消息时设置 hint，Worker checkpoint 处检查。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            client.set(
                _interrupt_hint_key(session_id),
                "1",
                ex=INTERRUPT_HINT_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                "Redis set_interrupt_hint failed session_id=%s: %s", session_id, e
            )
            return False

    def delete_interrupt_hint(self, session_id: str) -> None:
        """前端队列清空时删除 hint。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(_interrupt_hint_key(session_id))
        except Exception as e:
            logger.warning(
                "Redis delete_interrupt_hint failed session_id=%s: %s", session_id, e
            )

    def has_interrupt_hint(self, session_id: str) -> bool:
        """检查是否存在 interrupt hint。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            return client.exists(_interrupt_hint_key(session_id)) > 0
        except Exception:
            return False

    def set_interrupt_confirm(self, session_id: str) -> bool:
        """前端收到 checkpoint 后确认中断。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            client.set(
                _interrupt_confirm_key(session_id),
                "1",
                ex=INTERRUPT_CONFIRM_TTL_SEC,
            )
            return True
        except Exception as e:
            logger.warning(
                "Redis set_interrupt_confirm failed session_id=%s: %s", session_id, e
            )
            return False

    def has_interrupt_confirm(self, session_id: str) -> bool:
        """检查是否已确认中断。"""
        client = self.get_command_client()
        if not client:
            return False
        try:
            return client.exists(_interrupt_confirm_key(session_id)) > 0
        except Exception:
            return False

    def delete_interrupt_confirm(self, session_id: str) -> None:
        """清除中断确认（checkpoint 流程结束后）。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(_interrupt_confirm_key(session_id))
        except Exception as e:
            logger.warning(
                "Redis delete_interrupt_confirm failed session_id=%s: %s",
                session_id,
                e,
            )

    def cleanup_interrupt_keys(self, session_id: str) -> None:
        """清除该会话所有 interrupt 相关 key（run 结束时调用）。"""
        client = self.get_command_client()
        if not client:
            return
        try:
            client.delete(
                _interrupt_hint_key(session_id),
                _interrupt_confirm_key(session_id),
            )
        except Exception as e:
            logger.warning(
                "Redis cleanup_interrupt_keys failed session_id=%s: %s",
                session_id,
                e,
            )


@lru_cache(maxsize=1)
def get_redis_dao() -> RedisDao:
    return RedisDao()
