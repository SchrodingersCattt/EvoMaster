import base64
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime
from functools import lru_cache

from src.dao.chat_sessions_table import (
    WORKSPACE_PREF_UNSET,
    ChatSessionsTable,
    get_chat_sessions_table,
    session_row_to_item,
)
from src.dao.redis_dao import get_redis_dao
from src.services.tools_server_allowlist import is_user_in_admin_allowlist
from src.services.worker_registry_service import get_worker_registry_service
from src.utils.constant import REDIS_URL
from src.utils.exceptions import BadRequestErrorResponse
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)

# Redis 跨 worker 停止：channel 名，消息体为 session_id
REDIS_STOP_CHANNEL = "matmaster_chat:stop"


def _encode_session_list_cursor(updated_at: datetime, session_id: str) -> str:
    u = updated_at.isoformat() if updated_at is not None else ""
    raw = json.dumps({"u": u, "i": session_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_session_list_cursor(token: str) -> tuple[datetime, str] | None:
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        d = json.loads(raw.decode())
        u = d.get("u")
        i = d.get("i")
        if not isinstance(u, str) or not isinstance(i, str):
            return None
        cur_dt = datetime.fromisoformat(u)
        return cur_dt, i
    except Exception:
        return None


class RedisStopSubscriber:
    """Redis 停止订阅：在独立线程中监听 channel。run 仅在 Worker 上，停止由 Worker 轮询 Redis stop key 处理，API 收到消息无需动作，仅保留订阅以维持连接。"""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
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
            logger.info("Redis stop subscriber started, channel=%s", REDIS_STOP_CHANNEL)
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                # run 仅在 Worker 上，停止由 Worker 轮询 Redis stop key 处理；API 无需 set 本地 event，保留订阅线程即可
        except Exception as e:
            logger.warning("Redis stop subscriber exited: %s", e)
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
                logger.debug("Redis not configured, stop subscriber not started")
                return False
            if get_redis_dao().get_publish_client() is None:
                return False
            self._thread = threading.Thread(
                target=self._run,
                name="redis-stop-subscriber",
                daemon=True,
            )
            self._thread.start()
            self._started = True
        return True


# 仅存会话级运行时数据（如 bohrium_node_id）。history / task_ids / last_task_id、org_id / project_id 已持久化在 DB。
SESSIONS: dict[str, dict] = {}
DELETE_BLOCKED_STATUSES = {"active", "waiting"}


class ChatSessionsService:
    def __init__(self, table: ChatSessionsTable):
        self.table = table
        # 同一 session 同时只允许一个 agent 在跑，避免双开导致状态混乱
        self._sessions_in_run: set[str] = set()
        self._sessions_run_lock = threading.Lock()
        self._redis_stop_subscriber = RedisStopSubscriber()

    def can_access_session(
        self,
        session_id: str,
        user_id: str | None,
        *,
        allow_admin_read: bool = False,
    ) -> bool:
        """
        是否可访问该会话：
        - 会话不存在：仅登录用户可访问（用于新会话，后续 ensure_session 会创建）
        - 会话已分享：任何人可访问（含未登录）
        - 会话未分享：仅会话所有者可访问；若 ``allow_admin_read`` 且用户在 tools-server
          ``allowlist.admin`` 中，则允许只读场景（订阅 SSE、查看分享状态等）
        """
        row = self.table.get_session(session_id, include_deleted=True)
        if row and row.get("deleted_at") is not None:
            logger.info(
                "can_access_session: session_id=%s denied (session deleted)",
                session_id,
            )
            return False
        if not row:
            # 新会话尚未创建，仅允许已登录用户访问（会由 ensure_session 创建）
            allowed = user_id is not None
            if not allowed:
                logger.info(
                    "can_access_session: session_id=%s denied (session not in DB, user_id missing)",
                    session_id,
                )
            return allowed
        if row.get("is_shared"):
            return True
        if user_id is None:
            logger.info(
                "can_access_session: session_id=%s denied (not shared, no user_id)",
                session_id,
            )
            return False
        owner = row.get("user_id")
        if owner != user_id:
            if allow_admin_read and is_user_in_admin_allowlist(user_id):
                logger.info(
                    "can_access_session: session_id=%s admin read access user_id=%s",
                    session_id,
                    user_id,
                )
                return True
            logger.info(
                "can_access_session: session_id=%s denied (not owner: owner=%s user_id=%s)",
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

    def list_sessions(
        self,
        user_id: str,
        limit: int | None = 20,
        offset: int | None = 0,
        project_id: int | None = None,
    ) -> tuple[list[dict], int]:
        """返回 (sessions, total)。limit 默认 20，最大 100；不传或 0 表示使用默认。"""
        sessions = (
            self.table.list_sessions(
                user_id=user_id,
                limit=limit,
                offset=offset,
                project_id=project_id,
            )
            or []
        )
        total = self.table.count_sessions_by_user(user_id, project_id=project_id)
        return sessions, total

    def list_sessions_grouped_by_directory(
        self,
        user_id: str,
        project_id: int,
        per_group_limit: int,
    ) -> dict:
        """
        按 session_directory 聚合某项目下的会话。
        每个目录组内按 updated_at 倒序取首屏 per_group_limit 条（窗口函数）；
        未设置目录的会话归为一组 session_directory=null，且排在最后。
        """
        cap = max(1, min(50, per_group_limit))
        total = self.table.count_sessions_by_user(user_id, project_id=project_id)
        stats = self.table.aggregate_session_directory_stats(user_id, project_id)

        def sort_key(r: dict) -> tuple[int, float]:
            dk = r["dk"]
            is_unset = dk == "__UNSET__"
            ts = r.get("max_upd")
            if ts is not None and hasattr(ts, "timestamp"):
                tsv = -float(ts.timestamp())
            else:
                tsv = 0.0
            return (1 if is_unset else 0, tsv)

        stats_sorted = sorted(stats, key=sort_key)
        window_rows = self.table.list_sessions_windowed_first_per_directory(
            user_id, project_id, cap
        )
        by_dk: dict[str, list[dict]] = defaultdict(list)
        for row in window_rows:
            by_dk[row["dk"]].append(row)

        groups: list[dict] = []
        for agg in stats_sorted:
            dk = agg["dk"]
            session_count = int(agg["session_count"])
            raw_list = by_dk.get(dk, [])
            raw_list.sort(
                key=lambda r: (
                    r.get("updated_at") or datetime.min,
                    r.get("session_id") or "",
                ),
                reverse=True,
            )
            sessions = [session_row_to_item(r) for r in raw_list]
            has_more = session_count > len(sessions)
            next_cursor = None
            if has_more and raw_list:
                last = raw_list[-1]
                next_cursor = _encode_session_list_cursor(
                    last["updated_at"],
                    last["session_id"],
                )
            session_directory = None if dk == "__UNSET__" else dk
            groups.append(
                {
                    "session_directory": session_directory,
                    "session_count": session_count,
                    "sessions": sessions,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                }
            )

        return {
            "groups": groups,
            "total_sessions": total,
        }

    def list_sessions_more_in_directory(
        self,
        user_id: str,
        project_id: int,
        *,
        directory: str | None,
        limit: int,
        cursor_token: str,
    ) -> dict:
        """单目录组分页（加载更多）。cursor 指向上一页最后一条会话。"""
        cap = max(1, min(50, limit))
        decoded = _decode_session_list_cursor(cursor_token)
        if not decoded:
            raise BadRequestErrorResponse(msg="无效的分页游标")
        cur_ua, cur_sid = decoded
        rows = self.table.list_sessions_in_directory_paged(
            user_id,
            project_id,
            directory=directory,
            limit=cap,
            cursor_updated_at=cur_ua,
            cursor_session_id=cur_sid,
        )
        has_more = len(rows) > cap
        if has_more:
            rows = rows[:cap]
        sessions = [session_row_to_item(r) for r in rows]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_session_list_cursor(
                last["updated_at"],
                last["session_id"],
            )
        return {
            "sessions": sessions,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def get_active_sessions_count(self) -> int:
        """返回所有用户的活跃会话数量（status='active'），不限于当前用户。"""
        return self.table.count_active_sessions()

    def reset_stale_active_sessions(self) -> int:
        """
        将数据库中所有 status='active' 的会话重置为 'idle'。
        部署/重启后调用：上一进程若被强制终止，stream 可能未执行 release，导致 DB 残留 active。
        """
        return self.table.reset_all_active_to_idle()

    def reconcile_waiting_status(self, session_id: str, raw_status: object) -> str:
        """归一化会话状态，并校正 waiting。

        DB=waiting 但 Redis 已无 queued 标记时：若存在存活的 run_owner（worker 已接手）
        则视为 active 不重置；否则重置 DB 为 idle 并返回 idle，避免「还在跑却显示 idle」
        或「已结束仍卡 waiting」。
        """
        status = str(raw_status or "idle").strip() or "idle"
        if (
            status == "waiting"
            and REDIS_URL
            and not get_redis_dao().is_session_run_queued(session_id)
        ):
            registry = get_worker_registry_service()
            owner = registry.get_session_run_owner(session_id)
            if owner and registry.is_worker_alive(owner):
                return "active"
            self.reset_session_status_to_idle_in_db(session_id)
            return "idle"
        return status

    def get_session_status(self, session_id: str) -> str:
        """
        获取会话运行状态（来自 DB，部署/重启后与 reset_stale_active_sessions 一致）。
        用于流开头推送 session_status 事件，便于前端在重连后根据 idle 结束“未结束的 stream”状态。
        waiting=已入队未接手；若 DB 为 waiting 但 Redis 已无 queued 标记，则视为 idle 并重置 DB；
        若此时已有 run_owner 且存活，说明 worker 已接手，不重置并返回 active，避免「还在跑却显示 idle」。
        """
        row = self.table.get_session(session_id)
        if not row:
            return "idle"
        return self.reconcile_waiting_status(session_id, row.get("status"))

    def get_session_status_payload(self, session_id: str) -> dict:
        """
        获取会话状态及关联信息，用于 session_status 事件（status、last_task_id 等）。
        返回值含 source, type, status, session_id；可选 last_task_id。
        status 可为 idle | active | waiting（等待中=已入队未被 worker 接手）| failed（上一轮因 run_interrupted reason=restart 或 deploy 按失败结束）。
        若 DB 为 waiting 但 Redis 已无 queued 标记：若有 run_owner 且存活则视为 active 不重置，否则重置为 idle。
        """
        row = self.table.get_session(session_id)
        status = "idle"
        last_task_id = None
        if row:
            status = self.reconcile_waiting_status(session_id, row.get("status"))
            lt = row.get("last_task_id")
            if lt is not None and str(lt).strip():
                last_task_id = str(lt).strip()
        out = {
            "source": "System",
            "type": "session_status",
            "status": status,
            "session_id": session_id.strip(),
        }
        if last_task_id is not None:
            out["last_task_id"] = last_task_id
        return out

    def get_session(
        self, session_id: str, *, include_deleted: bool = False
    ) -> dict | None:
        """获取会话完整信息（含 org_id、project_id，用于 run_creds）。"""
        return self.table.get_session(session_id, include_deleted=include_deleted)

    def get_session_user_id(self, session_id: str) -> str | None:
        """获取会话所属用户 ID；会话不存在或无 user_id 时返回 None。"""
        row = self.table.get_session(session_id)
        if not row:
            return None
        uid = row.get("user_id")
        return str(uid) if uid is not None else None

    def set_session_bohrium(
        self,
        session_id: str,
        org_id: str | None = None,
        project_id: int | None = None,
    ) -> bool:
        """更新会话的 org_id、project_id，以库为准。"""
        return self.table.set_session_bohrium(
            session_id, org_id=org_id, project_id=project_id
        )

    def set_session_directory(
        self,
        session_id: str,
        directory: str | None,
        user_id: str,
    ) -> bool:
        """设置会话绑定的工作区目录。仅所有者可写。"""
        return self.table.set_session_directory(session_id, directory, user_id)

    def update_session_workspace_prefs(
        self,
        session_id: str,
        user_id: str,
        *,
        directory: object = WORKSPACE_PREF_UNSET,
        chat_mode: object = WORKSPACE_PREF_UNSET,
    ) -> bool:
        """更新会话工作区目录与/或 chat_mode；未传入的字段不更新。仅所有者可写。"""
        return self.table.update_session_workspace_prefs(
            session_id,
            user_id,
            directory=directory,
            chat_mode=chat_mode,
        )

    def set_session_chat_mode(
        self, session_id: str, chat_mode: str, user_id: str
    ) -> bool:
        """持久化会话偏好模式（direct|planner）。仅所有者可写。"""
        return self.table.update_session_workspace_prefs(
            session_id,
            user_id,
            directory=WORKSPACE_PREF_UNSET,
            chat_mode=chat_mode,
        )

    def set_session_title(
        self, session_id: str, title: str | None, user_id: str
    ) -> bool:
        """设置/清除会话自定义标题。仅会话所有者可写；title 为空表示清除。"""
        return self.table.set_session_title(session_id, user_id, title)

    def get_share_status(self, session_id: str) -> dict:
        """获取会话分享状态。返回 { \"enabled\": bool }，会话不存在返回 None。"""
        row = self.table.get_session(session_id)
        if not row:
            return {"enabled": False}
        return {"enabled": bool(row.get("is_shared"))}

    def set_share_status(self, session_id: str, enabled: bool, user_id: str) -> bool:
        """设置会话分享状态。仅会话所有者可设置。"""
        return self.table.set_share_status(
            session_id, is_shared=enabled, user_id=user_id
        )

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """软删除会话。仅会话所有者可删除；会清理内存中的 SESSIONS 与 run 占用。"""
        row = self.table.get_session(session_id)
        if not row:
            return False
        if row.get("user_id") != user_id:
            return False
        status = self.reconcile_waiting_status(session_id, row.get("status"))
        if status in DELETE_BLOCKED_STATUSES:
            return False
        self._clear_deleted_session_runtime([session_id])
        return self.table.delete_session(session_id, user_id)

    def delete_sessions_by_directory(
        self,
        *,
        user_id: str,
        project_id: int,
        directory: str | None,
    ) -> dict:
        """
        整体软删除某个 session_directory 组。

        若该组内存在 active/waiting，会整组拒绝删除，避免用户以为目录已清空但实际残留运行会话。
        """
        rows = self.table.list_session_delete_candidates_by_directory(
            user_id,
            project_id,
            directory=directory,
        )
        if not rows:
            return {
                "deleted_count": 0,
                "blocked_count": 0,
                "blocked_statuses": [],
            }

        session_ids: list[str] = []
        blocked_statuses: set[str] = set()
        blocked_count = 0
        for row in rows:
            sid = str(row.get("session_id") or "").strip()
            if not sid:
                continue
            session_ids.append(sid)
            status = self.reconcile_waiting_status(sid, row.get("status"))
            if status in DELETE_BLOCKED_STATUSES:
                blocked_count += 1
                blocked_statuses.add(status)

        if blocked_count > 0:
            return {
                "deleted_count": 0,
                "blocked_count": blocked_count,
                "blocked_statuses": sorted(blocked_statuses),
            }

        self._clear_deleted_session_runtime(session_ids)
        deleted_count = self.table.soft_delete_sessions_by_ids(session_ids, user_id)
        return {
            "deleted_count": deleted_count,
            "blocked_count": 0,
            "blocked_statuses": [],
        }

    def _clear_deleted_session_runtime(self, session_ids: list[str]) -> None:
        """软删除前清理 API 进程内和 worker registry 中的会话运行态。"""
        clean_ids = [sid.strip() for sid in session_ids if sid and sid.strip()]
        if not clean_ids:
            return
        registry = get_worker_registry_service()
        with self._sessions_run_lock:
            for sid in clean_ids:
                SESSIONS.pop(sid, None)
                self._sessions_in_run.discard(sid)
        for sid in clean_ids:
            registry.delete_session_run_owner(sid)

    def try_acquire_session_run(self, session_id: str) -> tuple[bool, str | None]:
        """
        若该 session 当前没有在跑的 agent 则占用并返回 (True, None)，否则返回 (False, reason)。
        reason 为 'already_in_run'（本进程已有 run）或 'db_update_failed'（UPDATE 未命中行，通常为会话尚未落库或 Worker 与 API 不同库）。
        """
        with self._sessions_run_lock:
            if session_id in self._sessions_in_run:
                return False, "already_in_run"
            self._sessions_in_run.add(session_id)
        if not self.table.set_session_status(session_id, "active"):
            with self._sessions_run_lock:
                self._sessions_in_run.discard(session_id)
            logger.warning(
                "try_acquire_session_run: set_session_status(active) failed session_id=%s "
                "(session row may not exist: ensure API and Worker use same DB)",
                session_id,
            )
            return False, "db_update_failed"
        worker_id = get_worker_id()
        get_worker_registry_service().set_session_run_owner(session_id, worker_id)
        if REDIS_URL:
            get_redis_dao().delete_session_run_queued(session_id)
        logger.info(
            "try_acquire_session_run: acquired session_id=%s worker_id=%s",
            session_id,
            worker_id,
        )
        return True, None

    def release_session_run(self, session_id: str, run_success: bool = True) -> None:
        """释放该 session 的“正在运行”占用（在 run 结束时调用）。
        run_success=False 时会话状态置为 failed，否则置为 idle。"""
        worker_id = get_worker_id()
        target_status = "idle" if run_success else "failed"
        logger.info(
            "release_session_run: session_id=%s worker_id=%s status=%s",
            session_id,
            worker_id,
            target_status,
        )
        with self._sessions_run_lock:
            self._sessions_in_run.discard(session_id)
        self.table.set_session_status(session_id, target_status)
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
            "discard_session_run_from_this_pod: session_id=%s worker_id=%s",
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
        self.table.set_session_status(session_id.strip(), "idle")

    def set_session_last_task(
        self, session_id: str, task_id: str, user_id: str | None = None
    ) -> None:
        """设置会话当前 task_id（持久化到 DB）。"""
        self.ensure_session(session_id, user_id=user_id)
        self.table.set_session_last_task(session_id, task_id)

    def stop_session_run(self, session_id: str) -> bool:
        """
        请求终止该会话当前正在运行的 agent。
        通过 Redis 发布 stop 消息并写入 stop key，Worker 轮询 Redis 停止 run。
        """
        sid = session_id.strip()
        redis_dao = get_redis_dao()
        redis_dao.publish(REDIS_STOP_CHANNEL, sid)
        ctx = redis_dao.get_interaction_run_context(sid)
        task_id = (ctx.get("task_id", "") or "").strip() if ctx else ""
        if redis_dao.set_stop_requested(sid, task_id):
            logger.debug(
                "stop_session_run: set_stop_requested session_id=%s task_id=%s",
                sid,
                task_id or "(session-only)",
            )
        return True

    def start_redis_stop_subscriber(self) -> bool:
        """
        启动 Redis 停止订阅线程（每个 worker 一个）。若未配置 Redis 则不启动。
        在 app lifespan 中调用一次即可。
        """
        return self._redis_stop_subscriber.start()


@lru_cache
def get_sessions_service() -> ChatSessionsService:
    return ChatSessionsService(get_chat_sessions_table())
