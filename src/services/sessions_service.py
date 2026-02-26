import importlib
import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path

from src.dao.chat_sessions_table import ChatSessionsTable, get_chat_sessions_table

logger = logging.getLogger(__name__)

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

importlib.import_module('playground.mat_master.core.playground')

# 仅存会话级运行时数据（如 bohrium_credentials）。history / task_ids / last_task_id 已持久化在 DB。
SESSIONS: dict[str, dict] = {}


class ChatSessionsService:
    def __init__(self, table: ChatSessionsTable):
        self.table = table
        # 同一 session 同时只允许一个 agent 在跑，避免双开导致状态混乱
        self._sessions_in_run: set[str] = set()
        self._sessions_run_lock = threading.Lock()
        # session_id -> 当前 run 的 stop Event，cancel_run 会 set 该 event
        self._run_stop_events: dict[str, threading.Event] = {}

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
        """确保会话存在：DB 有记录且内存有 SESSIONS 槽（仅存 bohrium_credentials 等运行时数据）。"""
        if session_id in SESSIONS:
            return
        if user_id is not None:
            self.table.create_session(session_id, user_id=user_id)
        else:
            row = self.table.get_session(session_id)
            if not row:
                return
        SESSIONS[session_id] = {'bohrium_credentials': None}

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
        """
        row = self.table.get_session(session_id)
        if not row:
            return 'idle'
        return str(row.get('status') or 'idle').strip() or 'idle'

    def get_session_status_payload(self, session_id: str) -> dict:
        """
        获取会话状态及关联信息，用于 session_status 事件（status、last_task_id 等）。
        返回值含 source, type, status, session_id；可选 last_task_id。
        """
        row = self.table.get_session(session_id)
        status = 'idle'
        last_task_id = None
        if row:
            status = str(row.get('status') or 'idle').strip() or 'idle'
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

    def get_session_user_id(self, session_id: str) -> str | None:
        """获取会话所属用户 ID；会话不存在或无 user_id 时返回 None。"""
        row = self.table.get_session(session_id)
        if not row:
            return None
        uid = row.get('user_id')
        return str(uid) if uid is not None else None

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
        return self.table.delete_session(session_id, user_id)

    def try_acquire_session_run(self, session_id: str) -> bool:
        """若该 session 当前没有在跑的 agent 则占用并返回 True，否则返回 False。"""
        with self._sessions_run_lock:
            if session_id in self._sessions_in_run:
                return False
            self._sessions_in_run.add(session_id)
        self.table.set_session_status(session_id, 'active')
        return True

    def release_session_run(self, session_id: str) -> None:
        """释放该 session 的“正在运行”占用（在 run 结束时调用）。"""
        with self._sessions_run_lock:
            self._sessions_in_run.discard(session_id)
        self.table.set_session_status(session_id, 'idle')

    def set_session_last_task(
        self, session_id: str, task_id: str, user_id: str | None = None
    ) -> None:
        """设置会话当前 task_id（持久化到 DB）。"""
        self.ensure_session(session_id, user_id=user_id)
        self.table.set_session_last_task(session_id, task_id)

    def set_stop_event(self, session_id: str, stop_event: threading.Event) -> None:
        """注册会话的取消事件，cancel_run(session_id) 会 set 该 event。"""
        self._run_stop_events[session_id] = stop_event

    def clear_stop_event(self, session_id: str) -> None:
        """run 结束时移除该会话的 stop event。"""
        self._run_stop_events.pop(session_id, None)


@lru_cache
def get_sessions_service() -> ChatSessionsService:
    return ChatSessionsService(get_chat_sessions_table())
