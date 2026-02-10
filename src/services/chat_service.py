import asyncio
import importlib
import logging
import mimetypes
import queue
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.dao.chat_events_table import get_chat_events_table
from src.dao.chat_sessions_table import get_chat_sessions_table

logger = logging.getLogger(__name__)

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

importlib.import_module('playground.mat_master.core.playground')

RUN_ID_WEB = 'mat_master_web'
SESSION_ID_DEMO = 'demo_session'

SESSIONS: dict[str, dict] = {}
_run_stop_events: dict[str, threading.Event] = {}
_cached_pg = None
_playground_init_done = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)


def _runs_dir() -> Path:
    return _project_root / 'runs'


def _get_run_workspace_path(run_id: str, task_id: str | None = None) -> Path | None:
    runs = _runs_dir()
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


def _init_playground_sync() -> None:
    global _cached_pg
    try:
        from evomaster.core import get_playground_class

        config_path = _project_root / 'configs' / 'mat_master' / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class('mat_master', config_path=config_path)
        run_dir = _project_root / 'runs' / RUN_ID_WEB
        run_dir.mkdir(parents=True, exist_ok=True)
        pg.set_run_dir(run_dir)
        pg.setup()
        _cached_pg = pg
        logger.info('MatMaster chat: playground (tools, MCP, agent) initialized.')
    except Exception as e:
        logger.exception('MatMaster chat playground init failed: %s', e)
        _cached_pg = None
    finally:
        _playground_init_done.set()


async def init_playground() -> None:
    """启动时初始化 playground（在 lifespan 中调用）。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_playground_sync)


def _planner_ask_and_wait(
    prompt: str,
    send_cb: Callable[[dict], Awaitable[None]],
    loop: asyncio.AbstractEventLoop,
    reply_queue: queue.Queue,
) -> str:
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


# ---------- 对外接口（供 apis 调用） ----------


def start_session(user_id: str | None = None) -> str:
    """确保 demo 会话存在并返回 session_id。"""
    if SESSION_ID_DEMO not in SESSIONS:
        SESSIONS[SESSION_ID_DEMO] = {
            'history': [],
            'task_ids': [],
            'last_task_id': None,
            'bohrium_credentials': None,  # 继续在内存中存储，不存数据库
        }
    # 保存到数据库
    table = get_chat_sessions_table()
    if table:
        try:
            table.create_session(SESSION_ID_DEMO, user_id=user_id)
        except Exception as e:
            logger.error(f'保存会话到数据库失败: {e}', exc_info=True)
    return SESSION_ID_DEMO


def get_sessions(user_id: str) -> list[dict]:
    """返回会话列表 [{id, history_length}]。只返回该用户的会话。"""
    table = get_chat_sessions_table()
    if not table:
        logger.error('ChatSessionsTable 未初始化')
        return []
    try:
        sessions = table.get_sessions(user_id=user_id)
        return sessions or []
    except Exception as e:
        logger.error(f'从数据库获取会话列表失败: {e}', exc_info=True)
        return []


def get_session_events(session_id: str) -> list:
    """返回某会话的历史消息列表。"""
    events_table = get_chat_events_table()
    if not events_table:
        return []
    try:
        return events_table.get_session_events(session_id)
    except Exception as e:
        logger.error(f'从数据库获取会话历史失败: {e}', exc_info=True)
        return []


def get_session_run_info(session_id: str) -> dict:
    """返回某会话的 run_id、last_task_id、task_ids。"""
    # 优先从数据库读取
    table = get_chat_sessions_table()
    if table:
        try:
            run_info = table.get_session_run_info(session_id)
            if run_info:
                return run_info
        except Exception as e:
            logger.error(f'从数据库获取会话 run_info 失败: {e}', exc_info=True)
    # 降级到内存
    data = SESSIONS.get(session_id)
    if not data:
        return {'run_id': RUN_ID_WEB, 'last_task_id': None, 'task_ids': []}
    return {
        'run_id': RUN_ID_WEB,
        'last_task_id': data.get('last_task_id'),
        'task_ids': data.get('task_ids') or [],
    }


def list_session_files(session_id: str, path: str = '') -> dict | None:
    """
    列出会话工作区下的文件。成功返回 {run_id, path, entries, workspace_root, task_id}；
    若 run 或 workspace 不存在返回带空 entries 的 dict，路径非法返回 None（由 api 转 400/404）。
    """
    run_path = _runs_dir() / RUN_ID_WEB
    if not run_path.is_dir():
        return {
            'run_id': RUN_ID_WEB,
            'path': path or '.',
            'entries': [],
            'workspace_root': None,
            'task_id': None,
        }
    # 优先从数据库读取 task_id
    task_id = None
    table = get_chat_sessions_table()
    if table:
        try:
            run_info = table.get_session_run_info(session_id)
            task_id = run_info.get('last_task_id')
        except Exception as e:
            logger.error(f'从数据库获取 task_id 失败: {e}', exc_info=True)
    # 降级到内存
    if task_id is None:
        data = SESSIONS.get(session_id)
        task_id = (data or {}).get('last_task_id') if data else None
    if task_id is None:
        task_id = session_id
        (run_path / 'workspaces' / task_id).mkdir(parents=True, exist_ok=True)
    base = _get_run_workspace_path(RUN_ID_WEB, task_id=task_id)
    if not base or not base.is_dir():
        return {
            'run_id': RUN_ID_WEB,
            'path': path or '.',
            'entries': [],
            'workspace_root': None,
            'task_id': task_id,
        }
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append(
            {'name': p.name, 'path': str(rel).replace('\\', '/'), 'dir': p.is_dir()}
        )
    return {
        'run_id': RUN_ID_WEB,
        'path': path or '.',
        'entries': entries,
        'workspace_root': str(base),
        'task_id': task_id,
    }


def get_session_file_path(session_id: str, path: str) -> tuple[Path | None, str | None]:
    """
    解析会话工作区内文件路径。返回 (Path, media_type)，不存在或非法返回 (None, None)。
    """
    if not path or not path.strip():
        return None, None
    run_path = _runs_dir() / RUN_ID_WEB
    if not run_path.is_dir():
        return None, None
    # 优先从数据库读取 task_id
    task_id = None
    table = get_chat_sessions_table()
    if table:
        try:
            run_info = table.get_session_run_info(session_id)
            task_id = run_info.get('last_task_id')
        except Exception as e:
            logger.error(f'从数据库获取 task_id 失败: {e}', exc_info=True)
    # 降级到内存
    if task_id is None:
        data = SESSIONS.get(session_id)
        task_id = (data or {}).get('last_task_id') if data else None
    if task_id is None:
        task_id = session_id
    base = _get_run_workspace_path(RUN_ID_WEB, task_id=task_id)
    if not base or not base.is_dir():
        return None, None
    target = (base / path.strip()).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None, None
    if not target.exists() or target.is_dir():
        return None, None
    media_type, _ = mimetypes.guess_type(str(target), strict=False)
    return target, (media_type or 'application/octet-stream')


def get_share_history(session_id: str) -> list | None:
    """返回某会话历史用于分享；会话不存在返回 None。"""
    events_table = get_chat_events_table()
    sessions_table = get_chat_sessions_table()
    if not events_table or not sessions_table:
        return None
    try:
        history = events_table.get_session_events(session_id)
        if history:
            return history
        # 如果数据库中没有历史，检查会话是否存在
        session = sessions_table.get_session(session_id)
        if session:
            return []
        return None
    except Exception as e:
        logger.error(f'从数据库获取分享历史失败: {e}', exc_info=True)
        return None


def list_runs() -> list[dict]:
    """返回 mat_master_* run 列表 [{id, label}]。"""
    runs_list: list[dict] = []
    rd = _runs_dir()
    if rd.is_dir():
        for p in sorted(rd.iterdir(), key=lambda x: x.name, reverse=True):
            if p.is_dir() and p.name.startswith('mat_master_'):
                runs_list.append({'id': p.name, 'label': p.name})
    if not runs_list:
        runs_list.append(
            {'id': 'mat_master_web', 'label': 'mat_master_web (created on first run)'}
        )
    return runs_list


def list_run_files(
    run_id: str, path: str = '', task_id: str | None = None
) -> dict | None:
    """列出某 run 工作区文件。成功返回 {run_id, path, entries}，否则返回 None。"""
    base = _get_run_workspace_path(run_id, task_id=task_id)
    if not base or not base.is_dir():
        return None
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append(
            {'name': p.name, 'path': str(rel).replace('\\', '/'), 'dir': p.is_dir()}
        )
    return {'run_id': run_id, 'path': path or '.', 'entries': entries}


def get_run_file_path(
    run_id: str, path: str, task_id: str | None = None
) -> Path | None:
    """解析 run 工作区内文件路径，不存在或非法返回 None。"""
    if not path or not path.strip():
        return None
    base = _get_run_workspace_path(run_id, task_id=task_id)
    if not base or not base.is_dir():
        return None
    target = (base / path.strip()).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    if not target.exists() or target.is_dir():
        return None
    return target


def ensure_session(session_id: str, user_id: str) -> None:
    """确保会话存在（空 history/task_ids）。"""
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {
            'history': [],
            'task_ids': [],
            'last_task_id': None,
            'bohrium_credentials': None,  # 继续在内存中存储，不存数据库
        }
    # 保存到数据库
    table = get_chat_sessions_table()
    if table:
        try:
            table.create_session(session_id, user_id=user_id)
        except Exception as e:
            logger.error(f'保存会话到数据库失败: {e}', exc_info=True)


def add_history_event(session_id: str, payload: dict, user_id: str) -> None:
    """向会话历史追加一条事件。"""
    ensure_session(session_id, user_id=user_id)
    SESSIONS[session_id]['history'].append(payload)
    # 保存到数据库
    events_table = get_chat_events_table()
    if events_table:
        try:
            source = payload.get('source', 'System')
            event_type = payload.get('type', 'unknown')
            content = payload.get('content', '')
            task_id = payload.get('task_id')
            events_table.add_event(session_id, source, event_type, content, task_id)
        except Exception as e:
            logger.error(f'保存事件到数据库失败: {e}', exc_info=True)


def set_session_last_task(session_id: str, task_id: str, user_id: str) -> None:
    """设置会话当前 task_id 并加入 task_ids。"""
    ensure_session(session_id, user_id=user_id)
    SESSIONS[session_id].setdefault('task_ids', []).append(task_id)
    SESSIONS[session_id]['last_task_id'] = task_id
    # 保存到数据库
    table = get_chat_sessions_table()
    if table:
        try:
            table.set_session_last_task(session_id, task_id)
        except Exception as e:
            logger.error(f'保存 task_id 到数据库失败: {e}', exc_info=True)


def get_executor() -> ThreadPoolExecutor:
    """供 api 层 run_in_executor 使用。"""
    return _executor


def run_agent_sync(
    session_id: str,
    user_prompt: str,
    send_cb: Callable[[dict], Awaitable[None]],
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
    mode: str,
    planner_reply_queue: queue.Queue,
    task_id: str,
) -> None:
    """在后台线程中执行 agent，由 api 层 run_in_executor(executor, run_agent_sync, ...) 调用。"""
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
            SESSIONS[session_id] = {'history': [], 'task_ids': [], 'last_task_id': None}
        if event_type != 'log_line':
            SESSIONS[session_id]['history'].append(payload)
            # 保存到数据库
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
        if not _playground_init_done.is_set():
            logger.info(
                'run_agent_sync: playground not inited, running _init_playground_sync in thread (first request)'
            )
            _init_playground_sync()
        else:
            logger.debug('run_agent_sync: playground already inited')
        run_dir = _project_root / 'runs' / RUN_ID_WEB
        task_id = task_id or ('ws_' + uuid.uuid4().hex[:8])

        if _cached_pg is not None:
            pg = _cached_pg
            pg.set_run_dir(run_dir, task_id=task_id)
            logger.info(
                'run_agent_sync: using cached playground, run_dir=%s task_id=%s',
                run_dir,
                task_id,
            )
        else:
            logger.info('run_agent_sync: creating fresh playground')
            from evomaster.core import get_playground_class

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
            pg._planner_input_fn = lambda prompt: _planner_ask_and_wait(
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

        # 从内存获取 Bohrium 凭证并设置到 session 对象上（Bohrium 凭证不存数据库）
        session_data = SESSIONS.get(session_id, {})
        bohrium_creds = session_data.get('bohrium_credentials')
        if bohrium_creds and base.session:
            base.session._bohrium_credentials = bohrium_creds

        from playground.mat_master.service.stream_agent import StreamingMatMasterAgent

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
        logger.info('run_agent_sync: starting exp=%s task_id=%s', exp_name, task_id)
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
        # 发送 end 事件，通知 SSE 连接可以关闭
        try:
            event_callback(
                'System', 'end', 'Task completed, SSE connection can be closed.'
            )
        except Exception:
            pass
        _run_stop_events.pop(session_id, None)
        logger.debug(
            'run_agent_sync end: session_id=%s task_id=%s', session_id, task_id
        )


def set_stop_event(session_id: str, stop_event: threading.Event) -> None:
    """注册会话的取消事件，cancel_run(session_id) 会 set 该 event。"""
    _run_stop_events[session_id] = stop_event


def cancel_run(session_id: str) -> None:
    """请求取消该会话当前运行。"""
    if session_id in _run_stop_events:
        _run_stop_events[session_id].set()
