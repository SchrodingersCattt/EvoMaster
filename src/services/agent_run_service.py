"""Agent 执行服务：playground 初始化、线程池、run_agent_sync。"""

import asyncio
import gc
import importlib
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from evomaster.core import get_playground_class
from evomaster.utils import LLMConfig, create_llm
from evomaster.utils.types import TaskInstance
from playground.mat_master.service.confirm import ConfirmationManager
from playground.mat_master.service.stream_agent import StreamingMatMasterAgent
from src.dao.chat_events_table import get_chat_events_table
from src.dao.oss_io import upload_dir_to_oss
from src.dao.redis_dao import get_redis_dao
from src.services.agent_run_bohrium import (
    apply_run_credentials_to_session,
    cleanup_bohrium_after_run,
    load_run_credentials,
    setup_bohrium_for_run,
)
from src.services.chat_history import ChatHistoryConverter
from src.services.quota_service import use_quota
from src.services.sessions_service import get_sessions_service
from src.utils.chat_event_source import normalize_event_source
from src.utils.worker_id import get_worker_id

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 支持多用户并发：agent 运行在线程池中（默认 2 以降低内存占用，可设 CHAT_AGENT_MAX_WORKERS 覆盖）
_AGENT_MAX_WORKERS = int(os.environ.get('CHAT_AGENT_MAX_WORKERS', '2'))
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


def _is_streaming_thought_event(event_type: str, extra: dict[str, Any]) -> bool:
    """Return whether the event is an ephemeral thought stream marker/delta."""
    return event_type == 'thought' and extra.get('stream_state') in {
        'start',
        'streaming',
        'end',
    }


def _should_persist_event(event_type: str, extra: dict[str, Any]) -> bool:
    """Persist durable events only."""
    if event_type in {'log_line', 'llm_token'}:
        return False
    return not _is_streaming_thought_event(event_type, extra)


def _should_skip_push(
    mode: str, source: str, event_type: str, extra: dict[str, Any]
) -> bool:
    """Skip frontend push for internal-only thought variants."""
    if source == 'Planner' and _is_streaming_thought_event(event_type, extra):
        return True
    return (
        mode == 'direct'
        and event_type == 'thought'
        and not _is_streaming_thought_event(event_type, extra)
    )


class AgentRunService:
    """Agent 执行服务：playground 初始化、线程池、同步执行 run。"""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._executor = ThreadPoolExecutor(max_workers=_AGENT_MAX_WORKERS)
        self._playgrounds: dict[str, Any] = (
            {}
        )  # session_id -> pg，按 session 隔离，避免 B 的 run 覆盖 A 的 working_dir/SSH
        self._playground_init_done = threading.Event()

    def init_playground_sync(self) -> None:
        """预加载 playground 模块与配置，不创建 pg 实例；实际 run 按 session_id 在 _get_or_create_playground 中创建并在结束时 cleanup，避免长期持有导致内存增长。"""
        try:
            importlib.import_module('playground.mat_master.core.playground')
            config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
            if not config_path.exists():
                raise FileNotFoundError(f"Config not found: {config_path}")
            run_dir = _project_root / 'runs' / RUN_ID_WEB
            run_dir.mkdir(parents=True, exist_ok=True)
            logger.info('MatMaster chat: playground module and config ready.')
        except Exception as e:
            logger.exception('MatMaster chat playground init failed: %s', e)
        finally:
            self._playground_init_done.set()

    def _emit_mcp_event_safely(
        self,
        event_callback: Callable[..., None] | None,
        event_type: str,
        content: Any,
        **extra: Any,
    ) -> None:
        """Emit MCP progress event via run event_callback when available."""
        if not callable(event_callback):
            return
        try:
            event_callback('System', event_type, content, **extra)
        except Exception as e:
            logger.debug('emit MCP event failed type=%s err=%s', event_type, e)

    def _get_or_create_playground(
        self,
        session_id: str,
        event_callback: Callable[..., None] | None = None,
    ) -> Any:
        """按 session_id 返回或创建 playground，避免多用户共用同一 pg 导致 working_dir/SSH 串台。run 结束时 pop+cleanup 释放。"""
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
        if callable(event_callback):

            def _on_mcp_progress(progress: dict[str, Any]) -> None:
                if not isinstance(progress, dict):
                    return
                server_name = progress.get('server_name')
                transport = progress.get('transport')
                phase = str(progress.get('phase') or '')
                event_type = 'mcp_server_status' if server_name else 'mcp_connect'
                self._emit_mcp_event_safely(
                    event_callback,
                    event_type,
                    progress,
                    mcp_phase=phase,
                    mcp_server=server_name,
                    mcp_transport=transport,
                )

            pg._mcp_progress_callback = _on_mcp_progress
        self._emit_mcp_event_safely(
            event_callback,
            'mcp_connect',
            {
                'phase': 'start',
                'message': '正在初始化 Playground，并连接 MCP Servers...',
            },
            mcp_phase='start',
        )
        setup_started_at = time.monotonic()
        try:
            pg.setup()
        except Exception as e:
            elapsed_ms = int((time.monotonic() - setup_started_at) * 1000)
            self._emit_mcp_event_safely(
                event_callback,
                'mcp_connect',
                {
                    'phase': 'failed',
                    'elapsed_ms': elapsed_ms,
                    'error': str(e),
                    'message': 'MCP 初始化失败',
                },
                mcp_phase='failed',
            )
            raise
        elapsed_ms = int((time.monotonic() - setup_started_at) * 1000)
        self._emit_mcp_event_safely(
            event_callback,
            'mcp_connect',
            {
                'phase': 'ready',
                'elapsed_ms': elapsed_ms,
                'message': 'MCP 初始化完成',
            },
            mcp_phase='ready',
        )
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

    def _upload_workspace_to_oss(
        self,
        session_id: str,
        task_id: str,
        event_callback: Callable[..., None],
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
        loop: Optional[asyncio.AbstractEventLoop],
        stop_event: Any,
        mode: str,
        reply_queue: ReplyQueueLike | None,
        task_id: str,
        invocation_id: str | None = None,
        llm_override: str | None = None,
        model_override: str | None = None,
    ) -> None:
        """在后台线程中执行 agent，由 stream 层 run_in_executor 或 Worker 进程调用。
        loop 为 None 时（Worker）：send_cb 为同步调用，不投递到 asyncio；stop_event 可为带 is_set() 的 Redis 轮询对象。
        reply_queue 供 confirmation_request（planner / ask_human）共用，POST /confirmation_reply 写入。
        llm_override：本轮使用的 LLM 配置块名（如 litellm/azure/deepseek），不传则用 agent 默认。
        model_override：本轮使用的模型名（如 gemini-3-flash-preview、azure/gpt-5），覆盖所选 LLM 配置里的 model。
        """
        prompt_preview = (
            (user_prompt[:80] + '...') if len(user_prompt) > 80 else user_prompt
        )
        logger.info(
            'run_agent_sync start: session_id=%s task_id=%s mode=%s prompt_len=%s preview=%s worker_id=%s',
            session_id,
            task_id,
            mode,
            len(user_prompt),
            prompt_preview,
            get_worker_id(),
        )
        run_started_at = time.monotonic()

        # 仅在 workspace 真有新/改/删文件时才上传：用快照比对，并用短防抖避免每次 tool 都扫目录
        _last_workspace_snapshot: list[frozenset[tuple[str, float, int]] | None] = [
            None
        ]
        _last_workspace_check_time: list[float] = [0.0]
        _ssh_attached = False
        _task_completed = False

        def event_callback(
            source: str, event_type: str, content: Any, **extra: Any
        ) -> None:
            raw_source = str(source or '').strip()
            source = normalize_event_source(source)
            payload = {
                'source': source,
                'type': event_type,
                'content': content,
                'session_id': session_id,
                'task_id': task_id,
            }
            if invocation_id is not None:
                payload['invocation_id'] = invocation_id
            if event_type == 'end':
                payload['task_completed'] = _task_completed
            payload.update(extra)
            if _should_persist_event(event_type, extra):
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
            # Planner 的原始流式 JSON thought 仅供内部消费；Direct 的完整 thought 仅入库不重复推送。
            skip_push = _should_skip_push(mode, raw_source, event_type, extra)
            if not skip_push:
                if loop is not None and asyncio.iscoroutinefunction(send_cb):
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
        run_result = None
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

            pg = self._get_or_create_playground(
                session_id, event_callback=event_callback
            )
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

            run_creds, user_id_for_ak, org_id = load_run_credentials(
                self._sessions_service, session_id
            )
            apply_run_credentials_to_session(base.session, run_creds)
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

            bohrium_setup = setup_bohrium_for_run(
                session_id=session_id,
                pg=pg,
                base=base,
                run_creds=run_creds,
                user_id_for_ak=user_id_for_ak,
                org_id=org_id,
                event_callback=event_callback,
                run_started_at=run_started_at,
            )
            _ssh_attached = bohrium_setup.ssh_attached
            if bohrium_setup.abort_result is not None:
                return bohrium_setup.abort_result

            # 本轮模型：支持 llm_override（换配置块）和 model_override（覆盖 model 字段，如 gemini-3-flash-preview / azure/gpt-5）
            run_llm = base.llm
            if llm_override or model_override:
                cfg = None
                if llm_override:
                    try:
                        cfg = pg.config_manager.get_llm_config(llm_override)
                    except Exception as e:
                        logger.warning(
                            'run_agent_sync: llm_override=%s failed (%s), use default session_id=%s',
                            llm_override,
                            e,
                            session_id,
                        )
                if cfg is None and not llm_override:
                    # 仅指定 model 时，先尝试从所有 llm 块中匹配完整配置；
                    # 未匹配则回退到当前 agent 的 LLM 配置作为 base
                    if model_override:
                        cfg = pg.config_manager.find_llm_config_by_model(model_override)
                    if cfg is None:
                        try:
                            cfg = base.llm.config.model_dump()
                        except Exception:
                            cfg = {}
                if cfg and isinstance(cfg, dict):
                    if model_override:
                        cfg = {**cfg, 'model': model_override}
                    try:
                        run_llm = create_llm(LLMConfig(**cfg))
                        logger.info(
                            'run_agent_sync: llm=%s model=%s session_id=%s task_id=%s',
                            llm_override or 'default',
                            cfg.get('model', 'default'),
                            session_id,
                            task_id,
                        )
                    except Exception as e:
                        logger.warning(
                            'run_agent_sync: create_llm failed (%s), use base session_id=%s',
                            e,
                            session_id,
                        )
            agent = StreamingMatMasterAgent(
                event_callback=event_callback,
                llm=run_llm,
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
                enabled_tool_names=getattr(base, 'enabled_tool_names', None),
                config_dict=config_dict,
            )
            agent.set_agent_name(getattr(base, '_agent_name', 'default'))
            agent._stop_event = stop_event
            if getattr(agent, 'session', None) is not None:
                agent.session._stop_event = stop_event
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

            # 多轮对话：从 DB 取历史事件
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
                    'run_agent_sync: get_session_events for history failed: %s',
                    e,
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
            exp.run(task=task, append_result=False)
            if stop_event.is_set():
                logger.info(
                    'run_agent_sync: task cancelled by user session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )
                event_callback('System', 'cancelled', 'Task cancelled by user.')
                run_result = (False, 'cancelled')
            else:
                _task_completed = True
                logger.info(
                    'run_agent_sync: task done session_id=%s task_id=%s',
                    session_id,
                    task_id,
                )
                # 任务成功后扣减配额（与 MatMaster 一致）；异常向上抛，由外层统一处理
                user_id = self._sessions_service.get_session_user_id(session_id)
                if user_id:
                    if loop is not None:
                        future = asyncio.run_coroutine_threadsafe(
                            use_quota(user_id), loop
                        )
                        future.result(timeout=10)
                    else:
                        asyncio.run(use_quota(user_id))
                event_callback('System', 'finish', 'Done')
                self._upload_workspace_to_oss(
                    session_id=session_id,
                    task_id=task_id,
                    event_callback=event_callback,
                )
                run_result = True
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
            cleanup_bohrium_after_run(
                session_id=session_id,
                sessions_service=self._sessions_service,
                event_callback=event_callback,
                pg_for_run=pg_for_run,
                ssh_attached=_ssh_attached,
            )
            # run 结束时清理 Redis stop key，避免 session 级 key 残留导致下一轮误判
            logger.info(
                'run_agent_sync: clear stop keys in finally session_id=%s task_id=%s',
                session_id,
                task_id,
            )
            get_redis_dao().delete_stop_requested(session_id, task_id)
            logger.info(
                'run_agent_sync end: session_id=%s task_id=%s worker_id=%s',
                session_id,
                task_id,
                get_worker_id(),
            )
            elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
            try:
                event_callback(
                    'System',
                    'end',
                    'Task completed, SSE connection can be closed.',
                    elapsed_ms=elapsed_ms,
                )
            except Exception:
                pass
            # run 结束后释放当前 agent 上的 trajectory/current_dialog 及大字符串，避免 pg.agent 长期持有导致多轮对话内存阶梯增长
            if pg_for_run is not None:
                try:
                    a = getattr(pg_for_run, 'agent', None)
                    if a is not None:
                        a.trajectory = None
                        a.current_dialog = None
                        a._initial_system_prompt = None
                        a._initial_user_prompt = None
                except Exception:
                    pass
            # run 结束后释放 playground
            pg = self._playgrounds.pop(session_id, None)
            if pg is not None:
                try:
                    pg.cleanup()
                except Exception as e:
                    logger.warning(
                        'playground cleanup on pop (MCP/session release): %s', e
                    )
                finally:
                    gc.collect()

        return (run_result, elapsed_ms)


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """启动时初始化 playground（在 lifespan 中调用）。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
