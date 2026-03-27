"""Synchronous agent run used by the WebSocket worker thread."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import uuid
from collections import Counter

from playground.mat_master.core.agent_config_helpers import (
    get_first_agent_config,
    resolve_mat_master_prompt_files,
)
from playground.mat_master.core.ask_human_helpers import (
    attach_ask_human_on_agent,
    get_ask_human_config_dict,
)
from playground.mat_master.core.dialog_history_helpers import (
    build_mat_master_discovery_task,
    trim_events_for_dialog_history,
)
from playground.mat_master.core.run_helpers import (
    is_streaming_thought_event,
    should_persist_chat_event,
    should_skip_push_for_frontend,
)
from playground.mat_master.core.workspace_resolver import (
    get_remote_session_workspace_root,
    load_workspace_config_dict,
)
from src.services.chat_history import ChatHistoryConverter
from src.utils.chat_event_source import normalize_event_source

from . import persistence, state
from .bootstrap import PROJECT_ROOT
from .paths import _get_run_id_web, _runs_dir

logger = logging.getLogger(__name__)
_REMOTE_WORKSPACE_ROOT = str(
    get_remote_session_workspace_root(
        load_workspace_config_dict(PROJECT_ROOT), project_root=PROJECT_ROOT
    )
)


def _build_dialog_history(events: list[dict]) -> list[dict]:
    """Build parent-only dialog history for the local Web debug backend."""
    parent_events = ChatHistoryConverter.exclude_spawn_events(events)
    if not parent_events:
        return []
    return ChatHistoryConverter.events_to_dialog_messages(parent_events)


def _run_agent_sync(
    session_id: str,
    user_prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
    mode: str = 'direct',
    planner_reply_queue: queue.Queue | None = None,
    task_id: str | None = None,
    ask_human_queue: queue.Queue | None = None,
    bohrium_access_key: str | None = None,
    bohrium_project_id: int | None = None,
):
    """Run MatMaster in a thread (direct or planner exp); send_cb streams events."""
    logging.basicConfig(level=logging.INFO)
    run_done: threading.Event | None = None
    _msg_seq = 0

    def event_callback(source: str, event_type: str, content, **extra) -> None:
        nonlocal _msg_seq
        _msg_seq += 1
        raw_source = str(source or '').strip()
        source = normalize_event_source(source)
        payload = {
            'msg_id': _msg_seq,
            'source': source,
            'type': event_type,
            'content': content,
            'session_id': session_id,
        }
        if extra:
            payload.update(extra)
        if session_id not in state.SESSIONS:
            state.SESSIONS[session_id] = {'history': [], 'last_task_id': None}
        if event_type == 'assistant_state':
            state.SESSIONS[session_id]['history'].append(payload)
            persistence._persist_history_event(session_id, payload)
            return
        _is_streaming_thought = is_streaming_thought_event(event_type, extra)
        if should_persist_chat_event(event_type, extra):
            state.SESSIONS[session_id]['history'].append(payload)
            persistence._persist_history_event(session_id, payload)
        if should_skip_push_for_frontend(mode, raw_source, event_type, extra):
            return
        if _is_streaming_thought and extra.get('stream_state') == 'streaming':
            asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
        else:
            future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass

    pg = None
    bohrium_node_id = None
    _ssh_attached = False
    access_key = ''
    try:
        state._playground_init_done.wait(timeout=300)
        run_dir = _runs_dir() / _get_run_id_web()
        task_id = task_id or ('ws_' + uuid.uuid4().hex[:8])

        if state._cached_pg is not None:
            pg = state._cached_pg
            pg.set_run_dir(run_dir, task_id=task_id, session_id=session_id)
            pg._setup_trajectory_file()
        else:
            from evomaster.core import get_playground_class

            config_path = PROJECT_ROOT / 'configs' / 'mat_master' / 'config.yaml'
            if not config_path.exists():
                raise FileNotFoundError(f'Config not found: {config_path}')
            pg = get_playground_class('mat_master', config_path=config_path)
            pg.set_run_dir(run_dir, task_id=task_id, session_id=session_id)
            pg.setup()
            pg._setup_trajectory_file()

        run_done = threading.Event()

        mode = (mode or 'direct').strip().lower() or 'direct'
        pg.set_mode(mode)

        pg._planner_output_callback = event_callback

        access_key = (bohrium_access_key or '').strip()
        if access_key and bohrium_project_id is not None:
            try:
                from src.services.bohrium_node_service import get_bohrium_node_service

                node_svc = get_bohrium_node_service()
                node_info = node_svc.create_node(access_key, int(bohrium_project_id))
                bohrium_node_id = node_info.get('node_id')
                if bohrium_node_id is not None:
                    event_callback(
                        'System',
                        'bohrium_node',
                        {
                            'node_id': bohrium_node_id,
                            'status': 'created',
                            'message': '节点已创建，正在等待就绪...',
                        },
                    )
                    node_info = node_svc.wait_until_ready(access_key, bohrium_node_id)
                    node_ip = node_info.get('ip')
                    node_domain = node_info.get('domain') or ''
                    node_pwd = node_info.get('password')
                    node_user = node_info.get('node_user') or 'root'
                    ssh_host = node_domain or node_ip
                    event_callback(
                        'System',
                        'bohrium_node',
                        {
                            'node_id': bohrium_node_id,
                            'status': 'ready',
                            'ip': node_ip,
                            'domain': node_domain,
                            'message': 'Bohrium 节点已就绪',
                        },
                    )
                    if ssh_host:
                        pg.attach_ssh_session(
                            host=ssh_host,
                            username=node_user,
                            password=node_pwd,
                            working_dir=_REMOTE_WORKSPACE_ROOT,
                        )
                        _ssh_attached = True
                        logger.info(
                            'SSH session attached to Bohrium node host=%s workspace=%s',
                            ssh_host,
                            _REMOTE_WORKSPACE_ROOT,
                        )
                        event_callback(
                            'System', 'status', f'已连接到 Bohrium 节点 {ssh_host}'
                        )
                        pg.session._bohrium_credentials = {
                            'access_key': access_key,
                            'project_id': int(bohrium_project_id),
                        }
                        logger.info(
                            'Bohrium credentials stored on session (_bohrium_credentials) '
                            'for skill remote env injection'
                        )
                        try:
                            pg.sync_skills_to_remote()
                            event_callback(
                                'System', 'status', 'Skills 已同步到远程节点'
                            )
                        except Exception as e:
                            logger.warning(
                                'sync_skills_to_remote failed: %s', e, exc_info=True
                            )
            except Exception as e:
                logger.warning('Auto create Bohrium node failed: %s', e, exc_info=True)
                event_callback(
                    'System',
                    'status',
                    f'自动创建 Bohrium 节点失败: {e}，继续使用当前环境运行',
                )

        base = pg.agent
        config_dict = pg.config.model_dump()
        agent_config = get_first_agent_config(config_dict)
        system_prompt_file, user_prompt_file, prompt_format_kwargs = (
            resolve_mat_master_prompt_files(pg.config_dir, agent_config)
        )

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
            enabled_tool_names=getattr(base, 'enabled_tool_names', None),
            direct_max_workers=getattr(base, '_direct_max_workers', 4),
            rate_limit=getattr(base, '_rate_limit', None),
            config_dict=config_dict,
        )
        agent.set_agent_name(getattr(base, '_agent_name', 'default'))
        agent._stop_event = stop_event
        if base.session is not None:
            base.session._stop_event = stop_event
        for tool in getattr(base, 'tools', []) or []:
            tool._stop_event = stop_event
        if ask_human_queue is not None:
            attach_ask_human_on_agent(
                agent,
                ask_human_queue,
                event_callback,
                get_ask_human_config_dict(config_dict),
            )

        pg.agent = agent
        exp = pg._create_exp()
        exp.set_run_dir(run_dir)
        event_callback('MatMaster', 'exp_run', exp.__class__.__name__)

        persistence._heal_orphaned_tool_calls(session_id)

        all_events = list(state.SESSIONS.get(session_id, {}).get('history', []))
        prior_events = trim_events_for_dialog_history(
            all_events, state.DIALOG_HISTORY_MAX_EVENTS
        )
        dialog_history = _build_dialog_history(prior_events)
        if prior_events:
            ev_types = Counter((e.get('type') or '?') for e in prior_events)
            logger.info(
                '_run_agent_sync: dialog_history session_id=%s task_id=%s '
                'raw_events=%s event_types=%s out_msgs=%s chain=%s',
                session_id,
                task_id,
                len(prior_events),
                dict(ev_types),
                len(dialog_history),
                ChatHistoryConverter.summarize_dialog_messages_for_log(dialog_history),
            )

        task = build_mat_master_discovery_task(task_id, user_prompt, dialog_history)
        exp.run(task=task)
        if stop_event.is_set():
            event_callback('System', 'cancelled', 'Task cancelled by user.')
        else:
            event_callback('System', 'finish', 'Done')
    except Exception as e:
        event_callback('System', 'error', str(e))
        raise
    finally:
        if _ssh_attached and pg is not None:
            try:
                pg.detach_session()
                pg._setup_session()
                logger.info('SSH session detached, default session restored')
            except Exception as e:
                logger.warning('Session restore failed: %s', e)
        if bohrium_node_id is not None:
            try:
                from src.services.bohrium_node_service import get_bohrium_node_service

                get_bohrium_node_service().destroy_node(
                    access_key,
                    int(bohrium_node_id),
                    int(bohrium_project_id or 0),
                )
                logger.info('Bohrium node destroyed node_id=%s', bohrium_node_id)
                event_callback(
                    'System',
                    'bohrium_node',
                    {
                        'node_id': bohrium_node_id,
                        'status': 'destroyed',
                        'message': '节点已销毁',
                    },
                )
            except Exception as e:
                logger.warning('Auto destroy Bohrium node failed: %s', e, exc_info=True)
        if run_done is not None:
            run_done.set()
        state._run_stop_events.pop(session_id, None)
        state._pending_cancel.discard(session_id)
