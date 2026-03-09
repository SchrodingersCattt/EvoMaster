import logging
import threading
from functools import lru_cache
from typing import Optional

from src.dao.chat_sessions_table import ChatSessionsTable, get_chat_sessions_table
from src.dao.redis_dao import get_redis_dao
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import REDIS_URL
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)

# Redis 跨 worker 停止：channel 名，消息体为 session_id
REDIS_STOP_CHANNEL = 'matmaster_chat:stop'


class RedisStopSubscriber:
    """Redis 停止订阅：在独立线程中监听 channel，收到 session_id 后调用本进程的 stop_session_run。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()

    def _run(self) -> None:
        client = get_redis_dao().create_client()
        if client is None:
            return
        pubsub = None
        try:
            pubsub = client.pubsub()
            pubsub.subscribe(REDIS_STOP_CHANNEL)
            logger.info('Redis stop subscriber started, channel=%s', REDIS_STOP_CHANNEL)
            for message in pubsub.listen():
                if message.get('type') != 'message':
                    continue
                sid = (message.get('data') or '').strip()
                if not sid:
                    continue
                try:
                    if get_sessions_service().stop_session_run(sid):
                        logger.info('Redis stop: set event for session_id=%s', sid)
                except Exception as e:
                    logger.warning(
                        'Redis stop: handle session_id=%s failed: %s', sid, e
                    )
        except Exception as e:
            logger.warning('Redis stop subscriber exited: %s', e)
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass

    def start(self) -> bool:
        """启动订阅线程。若未配置 Redis 或已启动则返回 False/True。"""
        with self._lock:
            if self._started:
                return True
            if not REDIS_URL:
                logger.debug('Redis not configured, stop subscriber not started')
                return False
            if get_redis_dao().get_publish_client() is None:
                return False
            self._thread = threading.Thread(
                target=self._run,
                name='redis-stop-subscriber',
                daemon=True,
            )
            self._thread.start()
            self._started = True
        return True


# 仅存会话级运行时数据（如 bohrium_node_id）。history / task_ids / last_task_id、org_id / project_id 已持久化在 DB。
SESSIONS: dict[str, dict] = {}


class ChatSessionsService:
    def __init__(self, table: ChatSessionsTable):
        self.table = table
        # 同一 session 同时只允许一个 agent 在跑，避免双开导致状态混乱
        self._sessions_in_run: set[str] = set()
        self._sessions_run_lock = threading.Lock()
        # session_id -> 当前 run 的 stop Event，stop_session_run 会 set 该 event
        self._run_stop_events: dict[str, threading.Event] = {}
        self._redis_stop_subscriber = RedisStopSubscriber()

    def can_access_session(self, session_id: str, user_id: str | None) -> bool:
        """
        是否可访问该会话：
        - 会话不存在：仅登录用户可访问（用于新会话，后续 ensure_session 会创建）
        - 会话已分享：任何人可访问（含未登录）
        - 会话未分享：仅会话所有者可访问
        """
        row = self.table.get_session(session_id)
        if not row:
            # 新会话尚未创建，仅允许已登录用户访问（会由 ensure_session 创建）
            allowed = user_id is not None
            if not allowed:
                logger.info(
                    'can_access_session: session_id=%s denied (session not in DB, user_id missing)',
                    session_id,
                )
            return allowed
        if row.get('is_shared'):
            return True
        if user_id is None:
            logger.info(
                'can_access_session: session_id=%s denied (not shared, no user_id)',
                session_id,
            )
            return False
        owner = row.get('user_id')
        if owner != user_id:
            logger.info(
                'can_access_session: session_id=%s denied (not owner: owner=%s user_id=%s)',
                session_id,
                owner,
                user_id,
            )
            return False
        return True

    def ensure_session(self, session_id: str, user_id: str | None = None) -> None:
        """确保会话存在：DB 有记录且内存有 SESSIONS 槽（run 时存 bohrium_node_id 等）。"""
        if session_id in SESSIONS:
            return
        if user_id is not None:
            self.table.create_session(session_id, user_id=user_id)
        else:
            row = self.table.get_session(session_id)
            if not row:
                return
        SESSIONS[session_id] = {}

    def list_sessions(self, user_id: str) -> list[dict]:
        return self.table.list_sessions(user_id=user_id) or []

    def get_active_sessions_count(self) -> int:
        """返回所有用户的活跃会话数量（status='active'），不限于当前用户。"""
        return self.table.count_active_sessions()

    def reset_stale_active_sessions(self) -> int:
        """
        将数据库中所有 status='active' 的会话重置为 'idle'。
        部署/重启后调用：上一进程若被强制终止，stream 可能未执行 release，导致 DB 残留 active。
        """
        return self.table.reset_all_active_to_idle()

    def get_session_status(self, session_id: str) -> str:
        """
        获取会话运行状态（来自 DB，部署/重启后与 reset_stale_active_sessions 一致）。
        用于流开头推送 session_status 事件，便于前端在重连后根据 idle 结束“未结束的 stream”状态。
        waiting=已入队未接手；若 DB 为 waiting 但 Redis 已无 queued 标记，则视为 idle 并重置 DB；
        若此时已有 run_owner 且存活，说明 worker 已接手，不重置并返回 active，避免「还在跑却显示 idle」。
        """
        row = self.table.get_session(session_id)
        if not row:
            return 'idle'
        status = str(row.get('status') or 'idle').strip() or 'idle'
        if (
            status == 'waiting'
            and REDIS_URL
            and not get_redis_dao().is_session_run_queued(session_id)
        ):
            registry = get_worker_registry_service()
            owner = registry.get_session_run_owner(session_id)
            if owner and registry.is_worker_alive(owner):
                return 'active'
            self.reset_session_status_to_idle_in_db(session_id)
            return 'idle'
        return status

    def get_session_status_payload(self, session_id: str) -> dict:
        """
        获取会话状态及关联信息，用于 session_status 事件（status、last_task_id 等）。
        返回值含 source, type, status, session_id；可选 last_task_id。
        status 可为 idle | active | waiting（等待中=已入队未被 worker 接手）。
        若 DB 为 waiting 但 Redis 已无 queued 标记：若有 run_owner 且存活则视为 active 不重置，否则重置为 idle。
        """
        row = self.table.get_session(session_id)
        status = 'idle'
        last_task_id = None
        if row:
            status = str(row.get('status') or 'idle').strip() or 'idle'
            if (
                status == 'waiting'
                and REDIS_URL
                and not get_redis_dao().is_session_run_queued(session_id)
            ):
                registry = get_worker_registry_service()
                owner = registry.get_session_run_owner(session_id)
                if owner and registry.is_worker_alive(owner):
                    status = 'active'
                else:
                    self.reset_session_status_to_idle_in_db(session_id)
                    status = 'idle'
            lt = row.get('last_task_id')
            if lt is not None and str(lt).strip():
                last_task_id = str(lt).strip()
        out = {
            'source': 'System',
            'type': 'session_status',
            'status': status,
            'session_id': session_id.strip(),
        }
        if last_task_id is not None:
            out['last_task_id'] = last_task_id
        return out

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取会话完整信息（含 org_id、project_id，用于 run_creds）。"""
        return self.table.get_session(session_id)

    def get_session_user_id(self, session_id: str) -> str | None:
        """获取会话所属用户 ID；会话不存在或无 user_id 时返回 None。"""
        row = self.table.get_session(session_id)
        if not row:
            return None
        uid = row.get('user_id')
        return str(uid) if uid is not None else None

    def set_session_bohrium(
        self,
        session_id: str,
        org_id: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> bool:
        """更新会话的 org_id、project_id，以库为准。"""
        return self.table.set_session_bohrium(
            session_id, org_id=org_id, project_id=project_id
        )

    def get_share_status(self, session_id: str) -> dict:
        """获取会话分享状态。返回 { \"enabled\": bool }，会话不存在返回 None。"""
        row = self.table.get_session(session_id)
        return {'enabled': bool(row.get('is_shared'))}

    def set_share_status(self, session_id: str, enabled: bool, user_id: str) -> bool:
        """设置会话分享状态。仅会话所有者可设置。"""
        return self.table.set_share_status(
            session_id, is_shared=enabled, user_id=user_id
        )

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """删除会话。仅会话所有者可删除；会清理内存中的 SESSIONS 与 run 占用。"""
        row = self.table.get_session(session_id)
        if not row:
            return False
        if row.get('user_id') != user_id:
            return False
        SESSIONS.pop(session_id, None)
        with self._sessions_run_lock:
            self._sessions_in_run.discard(session_id)
        self._run_stop_events.pop(session_id, None)
        get_worker_registry_service().delete_session_run_owner(session_id)
        return self.table.delete_session(session_id, user_id)

    def try_acquire_session_run(self, session_id: str) -> tuple[bool, str | None]:
        """
        若该 session 当前没有在跑的 agent 则占用并返回 (True, None)，否则返回 (False, reason)。
        reason 为 'already_in_run'（本进程已有 run）或 'db_update_failed'（UPDATE 未命中行，通常为会话尚未落库或 Worker 与 API 不同库）。
        """
        with self._sessions_run_lock:
            if session_id in self._sessions_in_run:
                return False, 'already_in_run'
            self._sessions_in_run.add(session_id)
        if not self.table.set_session_status(session_id, 'active'):
            with self._sessions_run_lock:
                self._sessions_in_run.discard(session_id)
            logger.warning(
                'try_acquire_session_run: set_session_status(active) failed session_id=%s '
                '(session row may not exist: ensure API and Worker use same DB)',
                session_id,
            )
            return False, 'db_update_failed'
        worker_id = get_worker_id()
        get_worker_registry_service().set_session_run_owner(session_id, worker_id)
        if REDIS_URL:
            get_redis_dao().delete_session_run_queued(session_id)
        logger.info(
            'try_acquire_session_run: acquired session_id=%s worker_id=%s',
            session_id,
            worker_id,
        )
        return True, None

    def release_session_run(self, session_id: str) -> None:
        """释放该 session 的“正在运行”占用（在 run 结束时调用）。"""
        worker_id = get_worker_id()
        logger.info(
            'release_session_run: session_id=%s worker_id=%s',
            session_id,
            worker_id,
        )
        with self._sessions_run_lock:
            self._sessions_in_run.discard(session_id)
        self.table.set_session_status(session_id, 'idle')
        get_worker_registry_service().delete_session_run_owner(session_id)

    def set_session_status(self, session_id: str, status: str) -> bool:
        """设置会话状态（idle=空闲, active=运行中, waiting=已入队等待 worker 接手）。供入队等逻辑使用。"""
        return self.table.set_session_status(session_id.strip(), status)

    def discard_session_run_from_this_pod(self, session_id: str) -> None:
        """仅从本进程 _sessions_in_run 移除，不改 DB 与 Redis run_owner。
        队列模式下 API 入队成功后调用，使 subscribe 流走「run 在别的 pod」分支并监听 Redis，避免流永不关闭。"""
        sid = session_id.strip()
        with self._sessions_run_lock:
            self._sessions_in_run.discard(sid)
        logger.info(
            'discard_session_run_from_this_pod: session_id=%s worker_id=%s',
            sid,
            get_worker_id(),
        )

    def is_session_running_on_this_pod(self, session_id: str) -> bool:
        """当前进程是否正在跑该 session 的 agent（仅内存状态）。"""
        with self._sessions_run_lock:
            return session_id.strip() in self._sessions_in_run

    def is_session_run_on_another_pod(self, session_id: str) -> bool:
        """
        该会话的 run 是否在别的「仍存活的」worker 上。
        Redis 中有 run owner 且 owner != 本进程，且该 owner 的存活 key 仍存在（未过期）。
        重启后旧进程不再刷新存活 key，故不会误判为「在别的 pod 跑」。
        """
        registry = get_worker_registry_service()
        owner = registry.get_session_run_owner(session_id.strip())
        if owner is None or owner == get_worker_id():
            return False
        return registry.is_worker_alive(owner)

    def reset_session_status_to_idle_in_db(self, session_id: str) -> None:
        """
        仅将 DB 中该会话状态置为 idle，不碰内存。用于：部署/重启后，另一 pod 上的 run 已死，
        本 pod 在 subscribe 时发现 DB 仍为 active 则视为 stale，先重置 DB 再推送 run_interrupted。
        """
        self.table.set_session_status(session_id.strip(), 'idle')

    def set_session_last_task(
        self, session_id: str, task_id: str, user_id: str | None = None
    ) -> None:
        """设置会话当前 task_id（持久化到 DB）。"""
        self.ensure_session(session_id, user_id=user_id)
        self.table.set_session_last_task(session_id, task_id)

    def set_stop_event(self, session_id: str, stop_event: threading.Event) -> None:
        """注册会话的取消事件，stop_session_run(session_id) 会 set 该 event。"""
        self._run_stop_events[session_id] = stop_event

    def stop_session_run(self, session_id: str) -> bool:
        """
        请求终止该会话当前正在运行的 agent。
        先通过 Redis 广播（多 worker 时其他进程可收到），再在本进程 set stop event。
        若启用队列模式且 run 在 Worker，则同时写 Redis stop key，供 Worker 轮询。
        若有活跃 run 则设置 stop event 并返回 True；否则返回 False。
        """
        sid = session_id.strip()
        redis_dao = get_redis_dao()
        redis_dao.publish(REDIS_STOP_CHANNEL, sid)
        # 队列模式：run 在 Worker，Worker 轮询 Redis stop key
        ctx = redis_dao.get_confirmation_run_context(sid)
        if ctx and ctx.get('task_id'):
            redis_dao.set_stop_requested(sid, ctx.get('task_id', ''))
        ev = self._run_stop_events.get(sid)
        if ev is None:
            return False
        ev.set()
        return True

    def start_redis_stop_subscriber(self) -> bool:
        """
        启动 Redis 停止订阅线程（每个 worker 一个）。若未配置 Redis 则不启动。
        在 app lifespan 中调用一次即可。
        """
        return self._redis_stop_subscriber.start()

    def clear_stop_event(self, session_id: str) -> None:
        """run 结束时移除该会话的 stop event。"""
        self._run_stop_events.pop(session_id, None)


@lru_cache
def get_sessions_service() -> ChatSessionsService:
    return ChatSessionsService(get_chat_sessions_table())
