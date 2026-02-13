"""Agent 执行服务：playground 初始化、线程池、run_agent_sync。"""

import asyncio
import logging
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

from evomaster.core import get_playground_class
from playground.mat_master.service.stream_agent import StreamingMatMasterAgent
from src.dao.chat_events_table import get_chat_events_table
from src.dao.oss_io import upload_dir_to_oss
from src.services.sessions_service import SESSIONS, get_sessions_service

logger = logging.getLogger(__name__)

# 支持多用户并发：agent 运行在线程池中
_AGENT_MAX_WORKERS = int(os.environ.get('CHAT_AGENT_MAX_WORKERS', '4'))
if _AGENT_MAX_WORKERS < 1:
    _AGENT_MAX_WORKERS = 1

_project_root = Path(__file__).resolve().parent.parent.parent
RUN_ID_WEB = 'mat_master_web'


class AgentRunService:
    """Agent 执行服务：playground 初始化、线程池、同步执行 run。"""

    def __init__(self, sessions_service=None):
        self._sessions_service = sessions_service or get_sessions_service()
        self._executor = ThreadPoolExecutor(max_workers=_AGENT_MAX_WORKERS)
        self._cached_pg = None
        self._playground_init_done = threading.Event()

    def init_playground_sync(self) -> None:
        """同步初始化 playground（tools、MCP、agent），结果缓存在 self._cached_pg。"""
        try:
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

    def _planner_ask_and_wait(
        self,
        prompt: str,
        send_cb: Callable[[dict], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        reply_queue: queue.Queue,
    ) -> str:
        """发送 planner_ask 到前端并阻塞等待 reply_queue 中的用户回复。"""
        payload = {'source': 'Planner', 'type': 'planner_ask', 'content': prompt}
        future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass
        try:
            return reply_queue.get(timeout=300)
        except queue.Empty:
            return 'abort'

    def _upload_workspace_to_oss(
        self,
        session_id: str,
        task_id: str,
        event_callback: Callable[[str, str, Any], None],
    ) -> None:
        """将当前任务的工作目录上传到 OSS，并通过 event_callback 推送 workspace_uploaded 或 workspace_upload_error。"""
        workspace_path = self._get_run_workspace_path(RUN_ID_WEB, task_id=task_id)
        if not workspace_path or not workspace_path.is_dir():
            logger.debug(
                'skip OSS upload: no workspace session_id=%s task_id=%s',
                session_id,
                task_id,
            )
            return
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
        except Exception as e:
            logger.exception(
                'upload workspace to OSS failed session_id=%s task_id=%s: %s',
                session_id,
                task_id,
                e,
            )
            event_callback('System', 'workspace_upload_error', str(e))

    def run_agent_sync(
        self,
        session_id: str,
        user_prompt: str,
        send_cb: Callable[[dict], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
        mode: str,
        planner_reply_queue: queue.Queue,
        task_id: str,
    ) -> None:
        """在后台线程中执行 agent，由 stream 层 run_in_executor(executor, self.run_agent_sync, ...) 调用。"""
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

        def event_callback(source: str, event_type: str, content: Any) -> None:
            payload = {
                'source': source,
                'type': event_type,
                'content': content,
                'session_id': session_id,
                'task_id': task_id,
            }
            if session_id not in SESSIONS:
                SESSIONS[session_id] = {
                    'history': [],
                    'task_ids': [],
                    'last_task_id': None,
                }
            if event_type != 'log_line':
                SESSIONS[session_id]['history'].append(payload)
                events_table = get_chat_events_table()
                if events_table:
                    try:
                        events_table.add_event(
                            session_id, source, event_type, content, task_id
                        )
                    except Exception as e:
                        logger.error(f'保存事件到数据库失败: {e}', exc_info=True)
            future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass

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

            if self._cached_pg is not None:
                pg = self._cached_pg
                pg.set_run_dir(run_dir, task_id=task_id)
                logger.info(
                    'run_agent_sync: using cached playground, run_dir=%s task_id=%s',
                    run_dir,
                    task_id,
                )
            else:
                logger.info(
                    'run_agent_sync: creating fresh playground (cached init failed)'
                )
                config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
                if not config_path.exists():
                    raise FileNotFoundError(f"Config not found: {config_path}")
                pg = get_playground_class('mat_master', config_path=config_path)
                pg.set_run_dir(run_dir, task_id=task_id)
                pg.setup()

            mode = (mode or 'direct').strip().lower() or 'direct'
            pg.set_mode(mode)
            logger.info(
                'run_agent_sync: mode=%s planner_enabled=%s',
                mode,
                mode == 'planner' and planner_reply_queue is not None,
            )

            if mode == 'planner' and planner_reply_queue is not None:
                pg._planner_input_fn = lambda prompt: self._planner_ask_and_wait(
                    prompt, send_cb, loop, planner_reply_queue
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
            if bohrium_creds and base.session:
                base.session._bohrium_credentials = bohrium_creds

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
            )
            agent.set_agent_name(getattr(base, '_agent_name', 'default'))
            agent._stop_event = stop_event

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

            exp.run(task_description=user_prompt, task_id=task_id)
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
            try:
                event_callback(
                    'System',
                    'end',
                    'Task completed, SSE connection can be closed.',
                )
            except Exception:
                pass
            self._sessions_service.clear_stop_event(session_id)
            self._sessions_service.release_session_run(session_id)
            logger.debug(
                'run_agent_sync end: session_id=%s task_id=%s',
                session_id,
                task_id,
            )


@lru_cache
def get_agent_run_service() -> AgentRunService:
    return AgentRunService(sessions_service=get_sessions_service())


async def init_playground() -> None:
    """启动时初始化 playground（在 lifespan 中调用）。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_agent_run_service().init_playground_sync)
