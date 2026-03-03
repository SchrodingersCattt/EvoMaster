"""Agent 执行服务：playground 初始化、线程池、run_agent_sync。"""

import asyncio
import importlib
import logging
import os
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from evomaster.core import get_playground_class
from evomaster.utils.types import TaskInstance
from playground.mat_master.service.confirm import ConfirmationManager
from playground.mat_master.service.stream_agent import StreamingMatMasterAgent
from src.dao.bohrium_nodes_table import get_bohrium_nodes_table
from src.dao.chat_events_table import get_chat_events_table
from src.dao.oss_io import upload_dir_to_oss
from src.services.bohrium_node_service import get_bohrium_node_service
from src.services.chat_history import ChatHistoryConverter
from src.services.quota_service import use_quota
from src.services.sessions_service import SESSIONS, get_sessions_service
from src.services.user_service import UserService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 支持多用户并发：agent 运行在线程池中
_AGENT_MAX_WORKERS = int(os.environ.get('CHAT_AGENT_MAX_WORKERS', '4'))
if _AGENT_MAX_WORKERS < 1:
    _AGENT_MAX_WORKERS = 1

# 检测到 workspace 有变更后，至少间隔多少秒再做一次「扫描+比对」（避免每次 tool 都扫目录）
_WORKSPACE_CHECK_DEBOUNCE_SECONDS = float(
    os.environ.get('CHAT_WORKSPACE_CHECK_DEBOUNCE_SECONDS', '2')
)

# 多轮对话历史：最多取最近 N 条事件，避免 context 过长
_DIALOG_HISTORY_MAX_EVENTS = int(
    os.environ.get('CHAT_DIALOG_HISTORY_MAX_EVENTS', '500')
)

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'


@runtime_checkable
class ReplyQueueLike(Protocol):
    """确认回复队列抽象：支持写入回复/取消，阻塞获取。get 返回 None 表示取消。"""

    def put_content(self, content: str) -> None: ...

    def put_cancel(self) -> None: ...

    def get(self, timeout: float | None = None) -> str | None:
        """阻塞获取回复。返回 None 表示取消；超时抛出 queue.Empty。"""
        ...


class AgentRunService:
    """Agent 执行服务：playground 初始化、线程池、同步执行 run。"""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._executor = ThreadPoolExecutor(max_workers=_AGENT_MAX_WORKERS)
        self._cached_pg = None
        self._playgrounds: dict[str, Any] = (
            {}
        )  # session_id -> pg，按 session 隔离，避免 B 的 run 覆盖 A 的 working_dir/SSH
        self._playground_init_done = threading.Event()

    def init_playground_sync(self) -> None:
        """同步初始化默认 playground（tools、MCP、agent），结果缓存在 self._cached_pg；实际 run 按 session_id 用 _get_or_create_playground。"""
        try:
            importlib.import_module('playground.mat_master.core.playground')
            config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
            if not config_path.exists():
                raise FileNotFoundError(f"Config not found: {config_path}")
            pg = get_playground_class('mat_master', config_path=config_path)
            run_dir = _project_root / 'runs' / RUN_ID_WEB
            run_dir.mkdir(parents=True, exist_ok=True)
            pg.set_run_dir(run_dir)
            pg.setup()
            self._cached_pg = pg
            logger.info('MatMaster chat: playground (tools, MCP, agent) initialized.')
        except Exception as e:
            logger.exception('MatMaster chat playground init failed: %s', e)
            self._cached_pg = None
        finally:
            self._playground_init_done.set()

    def _get_or_create_playground(self, session_id: str) -> Any:
        """按 session_id 返回或创建 playground，避免多用户共用同一 pg 导致 working_dir/SSH 串台。"""
        if session_id in self._playgrounds:
            return self._playgrounds[session_id]
        self._playground_init_done.wait(timeout=300)
        importlib.import_module('playground.mat_master.core.playground')
        config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class('mat_master', config_path=config_path)
        run_dir = _project_root / 'runs' / RUN_ID_WEB
        run_dir.mkdir(parents=True, exist_ok=True)
        pg.set_run_dir(run_dir, task_id=session_id)
        pg.setup()
        self._playgrounds[session_id] = pg
        logger.debug('run_agent_sync: playground created for session_id=%s', session_id)
        return pg

    def get_executor(self) -> ThreadPoolExecutor:
        """返回用于运行 agent 的线程池，供 run_in_executor 使用。"""
        return self._executor

    def _get_run_workspace_path(
        self, run_id: str, task_id: str | None = None
    ) -> Path | None:
        """解析某次 run 的 workspace 目录路径。"""
        runs = _project_root / 'runs'
        run_path = runs / run_id
        if not run_path.is_dir():
            return None
        if task_id:
            ws = run_path / 'workspaces' / task_id
            if ws.is_dir():
                return ws
            return None
        ws = run_path / 'workspace'
        if ws.is_dir():
            return ws
        workspaces = run_path / 'workspaces'
        if workspaces.is_dir():
            subs = [p for p in workspaces.iterdir() if p.is_dir()]
            if subs:
                return max(subs, key=lambda p: p.stat().st_mtime)
        return run_path

    def _get_workspace_snapshot(
        self, workspace_path: Path
    ) -> frozenset[tuple[str, float, int]]:
        """对 workspace 目录做轻量快照：每个文件的 (相对路径, mtime, size)，用于检测是否有新/改/删。"""
        out: set[tuple[str, float, int]] = set()
        try:
            for f in workspace_path.rglob('*'):
                if not f.is_file():
                    continue
                try:
                    st = f.stat()
                    rel = str(f.relative_to(workspace_path)).replace('\\', '/')
                    out.add((rel, st.st_mtime, st.st_size))
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return frozenset(out)

    def _planner_ask_and_wait(
        self,
        prompt: str,
        send_cb: Callable[[dict], Any],
        loop: asyncio.AbstractEventLoop,
        reply_queue: ReplyQueueLike,
    ) -> str:
        """发送 planner_ask 到前端并阻塞等待 reply_queue 中的用户回复。"""
        payload = {'source': 'Planner', 'type': 'planner_ask', 'content': prompt}
        if asyncio.iscoroutinefunction(send_cb):
            future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
        else:
            send_cb(payload)
        try:
            reply = reply_queue.get(timeout=300)
            if reply is None:
                return 'abort'
            return reply
        except queue.Empty:
            return 'abort'

    def _upload_workspace_to_oss(
        self,
        session_id: str,
        task_id: str,
        event_callback: Callable[[str, str, Any], None],
    ) -> bool:
        """将当前任务的工作目录上传到 OSS，并通过 event_callback 推送 workspace_uploaded 或 workspace_upload_error。返回是否成功。"""
        workspace_path = self._get_run_workspace_path(RUN_ID_WEB, task_id=task_id)
        if not workspace_path or not workspace_path.is_dir():
            logger.debug(
                'skip OSS upload: no workspace session_id=%s task_id=%s',
                session_id,
                task_id,
            )
            return False
        try:
            urls, rel_paths = upload_dir_to_oss(
                workspace_path,
                key_prefix=f'matmaster_evo/chat_workspace/{session_id}',
            )
            event_callback(
                'System',
                'workspace_uploaded',
                {
                    'session_id': session_id,
                    'task_id': task_id,
                    'workspace_path': '',
                    'count': len(rel_paths),
                },
            )
            logger.info(
                'workspace uploaded to OSS session_id=%s task_id=%s files=%s',
                session_id,
                task_id,
                len(rel_paths),
            )
            return True
        except Exception as e:
            logger.exception(
                'upload workspace to OSS failed session_id=%s task_id=%s: %s',
                session_id,
                task_id,
                e,
            )
            event_callback('System', 'workspace_upload_error', str(e))
            return False

    def run_agent_sync(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Any],
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
        mode: str,
        reply_queue: ReplyQueueLike | None,
        task_id: str,
        invocation_id: str | None = None,
    ) -> None:
        """在后台线程中执行 agent，由 stream 层 run_in_executor(executor, self.run_agent_sync, ...) 调用。
        reply_queue 供 planner_ask 与 confirmation_request（ask_human）共用，POST /confirmation_reply 写入。
        """
        prompt_preview = (
            (user_prompt[:80] + '...') if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            'run_agent_sync start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s',
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
        )

        # 仅在 workspace 真有新/改/删文件时才上传：用快照比对，并用短防抖避免每次 tool 都扫目录
        _last_workspace_snapshot: list[frozenset[tuple[str, float, int]] | None] = [
            None
        ]
        _last_workspace_check_time: list[float] = [0.0]
        _ssh_attached = False

        def event_callback(source: str, event_type: str, content: Any) -> None:
            payload = {
                'source': source,
                'type': event_type,
                'content': content,
                'session_id': session_id,
                'task_id': task_id,
            }
            if invocation_id is not None:
                payload['invocation_id'] = invocation_id
            if event_type != 'log_line':
                events_table = get_chat_events_table()
                if events_table:
                    try:
                        events_table.add_event(
                            session_id,
                            source,
                            event_type,
                            content,
                            task_id,
                            invocation_id=invocation_id,
                        )
                    except Exception as e:
                        logger.error(f'保存事件到数据库失败: {e}', exc_info=True)
            if event_type == 'tool_result':
                logger.info(
                    'run_agent_sync: tool_result before send_cb session_id=%s',
                    session_id,
                )
            if asyncio.iscoroutinefunction(send_cb):
                future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
                try:
                    future.result(timeout=5)
                except Exception as e:
                    logger.warning(
                        'run_agent_sync: send_cb timeout or error (event may be in DB but not pushed), session_id=%s type=%s: %s',
                        session_id,
                        event_type,
                        e,
                    )
            else:
                send_cb(payload)
            if event_type == 'tool_result':
                logger.info(
                    'run_agent_sync: tool_result after send_cb session_id=%s',
                    session_id,
                )
            # tool 执行后：仅当距上次检查已过防抖时间时扫目录，若快照与上次上传不一致（真有新/改/删）才上传
            if event_type == 'tool_result':
                # 兜底：用 root logger 打一条，确保任意配置下都能看到
                logging.info(
                    '[run_agent_sync] tool_result callback entered session_id=%s task_id=%s _ssh_attached=%s',
                    session_id,
                    task_id,
                    _ssh_attached,
                )
                logger.info(
                    'run_agent_sync: event_callback tool_result received session_id=%s task_id=%s _ssh_attached=%s',
                    session_id,
                    task_id,
                    _ssh_attached,
                )
                # 当前 run 使用远程节点时，工作目录在节点上，本地 workspace 无新文件，跳过上传避免阻塞
                if _ssh_attached:
                    logger.info(
                        'run_agent_sync: skip workspace upload (SSH attached, workspace on node) session_id=%s',
                        session_id,
                    )
                    return
                now = time.monotonic()
                if (
                    now - _last_workspace_check_time[0]
                ) < _WORKSPACE_CHECK_DEBOUNCE_SECONDS:
                    logger.debug(
                        'run_agent_sync: skip workspace upload (debounce) session_id=%s',
                        session_id,
                    )
                    return
                _last_workspace_check_time[0] = now
                workspace_path = self._get_run_workspace_path(
                    RUN_ID_WEB, task_id=task_id
                )
                if not workspace_path or not workspace_path.is_dir():
                    logger.debug(
                        'run_agent_sync: skip workspace upload (no path or not dir) session_id=%s path=%s',
                        session_id,
                        workspace_path,
                    )
                    return
                current_snapshot = self._get_workspace_snapshot(workspace_path)
                if (
                    _last_workspace_snapshot[0] is not None
                    and current_snapshot == _last_workspace_snapshot[0]
                ):
                    logger.debug(
                        'run_agent_sync: skip workspace upload (snapshot unchanged) session_id=%s',
                        session_id,
                    )
                    return
                logger.info(
                    'run_agent_sync: workspace upload to OSS starting session_id=%s task_id=%s path=%s',
                    session_id,
                    task_id,
                    workspace_path,
                )
                if self._upload_workspace_to_oss(
                    session_id=session_id,
                    task_id=task_id,
                    event_callback=event_callback,
                ):
                    _last_workspace_snapshot[0] = current_snapshot
                logger.info(
                    'run_agent_sync: workspace upload to OSS done session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )

        pg_for_run = None
        try:
            if not self._playground_init_done.is_set():
                logger.info(
                    'run_agent_sync: playground not inited, running init_playground_sync in thread (first request)'
                )
                self.init_playground_sync()
            else:
                logger.debug('run_agent_sync: playground already inited')
            run_dir = _project_root / 'runs' / RUN_ID_WEB
            task_id = task_id or ('ws_' + uuid.uuid4().hex[:16])

            pg = self._get_or_create_playground(session_id)
            pg.set_run_dir(run_dir, task_id=task_id)
            pg_for_run = pg
            logger.debug(
                'run_agent_sync: using playground for session_id=%s run_dir=%s task_id=%s',
                session_id,
                run_dir,
                task_id,
            )

            mode = (mode or 'direct').strip().lower() or 'direct'
            if getattr(pg, 'set_mode', None) is not None:
                pg.set_mode(mode)
            logger.info(
                'run_agent_sync: mode=%s reply_queue=%s',
                mode,
                'set' if reply_queue else 'none',
            )

            if mode == 'planner' and reply_queue is not None:
                pg._planner_input_fn = lambda prompt: self._planner_ask_and_wait(
                    prompt, send_cb, loop, reply_queue
                )
            pg._planner_output_callback = event_callback

            base = pg.agent
            config_dict = pg.config.model_dump()
            agents_block = config_dict.get('agents')
            if isinstance(agents_block, dict) and agents_block:
                agent_config = next(iter(agents_block.values()))
            else:
                agent_config = config_dict.get('agent') or {}
            if not isinstance(agent_config, dict):
                agent_config = {}
            system_prompt_file = agent_config.get('system_prompt_file')
            user_prompt_file = agent_config.get('user_prompt_file')
            playground_base = Path(str(pg.config_dir).replace('configs', 'playground'))
            if system_prompt_file:
                p = Path(system_prompt_file)
                if not p.is_absolute():
                    system_prompt_file = str((playground_base / p).resolve())
            if user_prompt_file:
                p = Path(user_prompt_file)
                if not p.is_absolute():
                    user_prompt_file = str((playground_base / p).resolve())
            prompt_format_kwargs = agent_config.get('prompt_format_kwargs', {})

            session_data = SESSIONS.get(session_id, {})
            bohrium_creds = session_data.get('bohrium_credentials')
            # 运行时凭据：不沿用内存中的 access_key，统一由后端按 X-User-Id + X-Org-Id 拉取
            run_creds = dict(bohrium_creds) if isinstance(bohrium_creds, dict) else {}
            run_creds.pop('access_key', None)
            user_id_for_ak = self._sessions_service.get_session_user_id(session_id)
            org_id = (run_creds.get('org_id') or '').strip()
            if user_id_for_ak and org_id:
                run_creds['access_key'] = (
                    UserService.get_bohrium_access_key(user_id_for_ak, org_id) or ''
                )
            if run_creds and base.session:
                base.session._bohrium_credentials = run_creds
            # 便于排查「工具拿不到 ak」：run 开始时是否具备 user_id/org_id 及是否拉取到 ak
            if run_creds:
                has_ak = bool((run_creds.get('access_key') or '').strip())
                logger.info(
                    'run_agent_sync: bohrium creds session_id=%s has_user_id=%s has_org_id=%s has_ak=%s',
                    session_id,
                    bool(user_id_for_ak),
                    bool(org_id),
                    has_ak,
                )

            # stream 开始 run 时若有 Bohrium 凭证则获取/创建节点；有 user_id+org_id 时走复用表（run 结束只更新 last_used_at）
            if run_creds:
                project_id = run_creds.get('project_id')
                if project_id is not None:
                    project_id = int(project_id)
                access_key = (run_creds.get('access_key') or '').strip()
                if access_key and project_id is not None:
                    try:
                        node_svc = get_bohrium_node_service()
                        node_id = None
                        node_ip = None
                        node_pwd = None
                        use_reuse_table = bool(user_id_for_ak and org_id)
                        if not use_reuse_table and (user_id_for_ak or org_id):
                            logger.info(
                                'run_agent_sync: skip reuse table (missing user_id or org_id); '
                                'user_id=%s org_id=%r — 请确保请求带 X-User-Id 且上游带 X-Org-Id',
                                user_id_for_ak,
                                org_id or '(empty)',
                            )
                        if use_reuse_table:
                            nodes_table = get_bohrium_nodes_table()
                            row = nodes_table.find_one_for_reuse(
                                user_id_for_ak, org_id, project_id
                            )
                            if row:
                                node_id = int(row['node_id'])
                                node_info = node_svc.get_node_info(access_key, node_id)
                                if node_info and node_info.get('ip'):
                                    node_ip = node_info.get('ip')
                                    node_pwd = node_info.get('password')
                                    logger.info(
                                        'run_agent_sync: reusing Bohrium node node_id=%s ip=%s',
                                        node_id,
                                        node_ip,
                                    )
                                else:
                                    # 节点存在但未就绪（如已关机）：先尝试重启，失败再删表并新建
                                    try:
                                        creator_id = 0
                                        if user_id_for_ak:
                                            try:
                                                creator_id = int(user_id_for_ak)
                                            except (TypeError, ValueError):
                                                creator_id = 0
                                        node_svc.restart_node(
                                            access_key,
                                            node_id,
                                            project_id,
                                            creator_id=creator_id,
                                        )
                                        event_callback(
                                            'System',
                                            'bohrium_node',
                                            {
                                                'node_id': node_id,
                                                'status': 'created',
                                                'message': '节点已重启，正在等待就绪...',
                                            },
                                        )
                                        node_info = node_svc.wait_until_ready(
                                            access_key, node_id
                                        )
                                        node_ip = node_info.get('ip')
                                        node_pwd = node_info.get('password')
                                        logger.info(
                                            'run_agent_sync: restarted Bohrium node node_id=%s ip=%s',
                                            node_id,
                                            node_ip,
                                        )
                                    except Exception as restart_err:
                                        logger.warning(
                                            'run_agent_sync: restart node_id=%s failed, will create new: %s',
                                            node_id,
                                            restart_err,
                                        )
                                        nodes_table.delete_by_node(
                                            user_id_for_ak,
                                            org_id,
                                            project_id,
                                            node_id,
                                        )
                                        node_id = None
                        if node_id is None or node_ip is None:
                            node_info = node_svc.create_node(access_key, project_id)
                            node_id = node_info.get('node_id')
                            if node_id is not None:
                                event_callback(
                                    'System',
                                    'bohrium_node',
                                    {
                                        'node_id': node_id,
                                        'status': 'created',
                                        'message': '节点已创建，正在等待就绪...',
                                    },
                                )
                                node_info = node_svc.wait_until_ready(
                                    access_key, node_id
                                )
                                node_ip = node_info.get('ip')
                                node_pwd = node_info.get('password')
                                if use_reuse_table and user_id_for_ak and org_id:
                                    try:
                                        get_bohrium_nodes_table().insert_node(
                                            user_id_for_ak,
                                            org_id,
                                            project_id,
                                            node_id,
                                        )
                                        logger.info(
                                            'run_agent_sync: inserted node into evo_bohrium_nodes '
                                            'user_id=%s org_id=%s project_id=%s node_id=%s',
                                            user_id_for_ak,
                                            org_id,
                                            project_id,
                                            node_id,
                                        )
                                    except Exception as insert_err:
                                        logger.warning(
                                            'run_agent_sync: insert_node failed (table missing?): %s',
                                            insert_err,
                                            exc_info=True,
                                        )
                        if node_id is not None and node_ip:
                            if session_id not in SESSIONS:
                                SESSIONS[session_id] = {}
                            SESSIONS[session_id]['bohrium_node_id'] = node_id
                            event_callback(
                                'System',
                                'bohrium_node',
                                {
                                    'node_id': node_id,
                                    'status': 'ready',
                                    'ip': node_ip,
                                    'message': 'Bohrium 节点已就绪',
                                },
                            )
                            pg.attach_ssh_session(
                                host=node_ip,
                                password=node_pwd,
                                working_dir='/personal/workspace',
                                session_id=session_id,
                            )
                            # 保持 run 开始时注入的 run_creds（含 access_key），勿用 SESSIONS 的 bohrium_creds 覆盖
                            if base.session and run_creds:
                                base.session._bohrium_credentials = run_creds
                            _ssh_attached = True
                            logger.info(
                                'run_agent_sync: SSH session attached to Bohrium node ip=%s',
                                node_ip,
                            )
                            # 运行时清除节点上由平台注入的代理配置，避免 wget/curl 等卡住
                            try:
                                if pg.session and hasattr(pg.session, 'exec_bash'):
                                    pg.session.exec_bash(
                                        'rm -f /root/speedUp.sh /speedUp.sh; '
                                        "echo 'use_proxy = no' > /root/.wgetrc; "
                                        "echo '# proxy disabled' > /root/.curlrc",
                                        timeout=15,
                                    )
                            except Exception as clear_err:
                                logger.warning(
                                    'run_agent_sync: clear_remote_proxy failed: %s',
                                    clear_err,
                                )
                            try:
                                pg.sync_skills_to_remote()
                                event_callback(
                                    'System',
                                    'bohrium_node',
                                    {
                                        'node_id': node_id,
                                        'status': 'skills_synced',
                                        'ip': node_ip,
                                        'message': 'Skills 已同步到远程节点',
                                    },
                                )
                            except Exception as sync_err:
                                logger.warning(
                                    'sync_skills_to_remote failed: %s',
                                    sync_err,
                                    exc_info=True,
                                )
                            event_callback(
                                'System',
                                'bohrium_node',
                                {
                                    'node_id': node_id,
                                    'status': 'connected',
                                    'ip': node_ip,
                                    'message': f'已连接到 Bohrium 节点 {node_ip}',
                                },
                            )
                    except Exception as e:
                        logger.warning(
                            'run_agent_sync: auto create Bohrium node failed: %s',
                            e,
                            exc_info=True,
                        )
                        event_callback(
                            'System',
                            'status',
                            f'自动创建 Bohrium 节点失败: {e}，继续使用当前环境运行',
                        )

            agent = StreamingMatMasterAgent(
                event_callback=event_callback,
                llm=base.llm,
                session=base.session,
                tools=base.tools,
                system_prompt_file=system_prompt_file,
                user_prompt_file=user_prompt_file,
                prompt_format_kwargs=prompt_format_kwargs,
                config=base.config,
                skill_registry=base.skill_registry,
                output_config=base.output_config,
                config_dir=pg.config_dir,
                enable_tools=base.enable_tools,
                config_dict=config_dict,
            )
            agent.set_agent_name(getattr(base, '_agent_name', 'default'))
            agent._stop_event = stop_event
            if reply_queue is not None:
                agent._ask_human_queue = reply_queue
                try:
                    mat_master_block = (
                        config_dict.get('mat_master')
                        if isinstance(config_dict, dict)
                        else None
                    )
                    ah_cfg = (
                        mat_master_block.get('ask_human')
                        if isinstance(mat_master_block, dict)
                        else {}
                    ) or {}
                    agent._ask_human_config = ah_cfg
                    agent._confirm_manager = ConfirmationManager(
                        emitter=event_callback,
                        reply_queue=reply_queue,
                        default_timeout_sec=ah_cfg.get('timeout_seconds', 20),
                    )
                except Exception:
                    pass

            pg.agent = agent
            exp = pg._create_exp()
            exp.set_run_dir(run_dir)
            exp_name = exp.__class__.__name__
            logger.info(
                'run_agent_sync: starting exp=%s task_id=%s',
                exp_name,
                task_id,
            )
            event_callback('MatMaster', 'exp_run', exp_name)

            # 多轮对话：从 DB 取历史事件，转为 dialog_history，通过 task.meta 传入
            history_events = []
            try:
                events_table = get_chat_events_table()
                if events_table:
                    all_events = events_table.get_session_events(session_id) or []
                    if (
                        all_events
                        and all_events[-1].get('source') == 'User'
                        and all_events[-1].get('type') == 'query'
                    ):
                        history_events = all_events[:-1]
                    else:
                        history_events = all_events
                    if len(history_events) > _DIALOG_HISTORY_MAX_EVENTS:
                        history_events = history_events[-_DIALOG_HISTORY_MAX_EVENTS:]
            except Exception as e:
                logger.debug(
                    'run_agent_sync: get_session_events for history failed: %s', e
                )
            dialog_history = (
                ChatHistoryConverter.events_to_dialog_messages(history_events)
                if history_events
                else []
            )
            if dialog_history:
                logger.debug(
                    'run_agent_sync: multi-turn dialog_history session_id=%s messages=%s',
                    session_id,
                    len(dialog_history),
                )
            task = TaskInstance(
                task_id=task_id,
                task_type='discovery',
                description=user_prompt,
                meta={'dialog_history': dialog_history},
            )
            exp.run(task=task)
            if stop_event.is_set():
                logger.info(
                    'run_agent_sync: task cancelled by user session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )
                event_callback('System', 'cancelled', 'Task cancelled by user.')
            else:
                logger.info(
                    'run_agent_sync: task done session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )
                # 任务成功后扣减配额（与 MatMaster 一致）；异常向上抛，由外层统一处理
                user_id = self._sessions_service.get_session_user_id(session_id)
                if user_id:
                    future = asyncio.run_coroutine_threadsafe(use_quota(user_id), loop)
                    future.result(timeout=10)
                event_callback('System', 'finish', 'Done')
                self._upload_workspace_to_oss(
                    session_id=session_id,
                    task_id=task_id,
                    event_callback=event_callback,
                )
        except Exception as e:
            logger.exception(
                'run_agent_sync: error session_id=%s task_id=%s err=%s',
                session_id,
                task_id,
                e,
            )
            event_callback('System', 'error', str(e))
            raise
        finally:
            # 若本 run 连接了 Bohrium 节点 SSH，先断开并恢复默认 session，再销毁节点
            if _ssh_attached and pg_for_run is not None:
                try:
                    pg_for_run.detach_session()
                    pg_for_run._setup_session()
                    logger.info(
                        'run_agent_sync: SSH session detached, default session restored'
                    )
                except Exception as e:
                    logger.warning('run_agent_sync: session restore failed: %s', e)
            # 复用表场景：只更新 last_used_at，不销毁；否则销毁本 run 使用的节点
            session_data = SESSIONS.get(session_id, {})
            node_id = session_data.pop('bohrium_node_id', None)
            creds = session_data.get('bohrium_credentials') or {}
            org_id = (creds.get('org_id') or '').strip()
            project_id = creds.get('project_id')
            user_id = self._sessions_service.get_session_user_id(session_id)
            # 销毁节点时 access_key 不再存于 SESSIONS，按需拉取
            access_key = (creds.get('access_key') or '').strip()
            if not access_key and user_id and org_id:
                access_key = UserService.get_bohrium_access_key(user_id, org_id) or ''
            if node_id is not None and user_id and org_id and project_id is not None:
                try:
                    get_bohrium_nodes_table().update_last_used_at(
                        user_id, org_id, int(project_id), int(node_id)
                    )
                    logger.info(
                        'run_agent_sync: updated last_used_at for node_id=%s (reuse table)',
                        node_id,
                    )
                except Exception as e:
                    logger.warning(
                        'run_agent_sync: update_last_used_at failed node_id=%s: %s',
                        node_id,
                        e,
                    )
            elif node_id is not None:
                try:
                    event_callback(
                        'System',
                        'bohrium_node',
                        {
                            'node_id': node_id,
                            'status': 'destroyed',
                            'message': '节点已销毁',
                        },
                    )
                except Exception:
                    pass
                if access_key and project_id is not None:
                    try:
                        creator_id = 0
                        if user_id is not None:
                            try:
                                creator_id = int(user_id)
                            except (TypeError, ValueError):
                                creator_id = 0
                        get_bohrium_node_service().destroy_node(
                            access_key,
                            int(node_id),
                            int(project_id),
                            creator_id=creator_id,
                        )
                    except Exception as e:
                        logger.warning(
                            'run_agent_sync: auto destroy Bohrium node node_id=%s failed: %s',
                            node_id,
                            e,
                            exc_info=True,
                        )
            self._sessions_service.clear_stop_event(session_id)
            self._sessions_service.release_session_run(session_id)
            try:
                event_callback(
                    'System',
                    'end',
                    'Task completed, SSE connection can be closed.',
                )
            except Exception:
                pass
            logger.debug(
                'run_agent_sync end: session_id=%s task_id=%s',
                session_id,
                task_id,
            )
            # run 结束后该 session 的后续请求仅为读历史/workspace（DB/OSS），不再需要 pg，及时释放避免内存常驻
            self._playgrounds.pop(session_id, None)


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """启动时初始化 playground（在 lifespan 中调用）。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
