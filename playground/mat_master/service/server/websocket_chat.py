"""WebSocket /ws/chat endpoint for streaming MatMaster runs."""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from . import persistence, state
from .run_agent import _run_agent_sync


async def websocket_chat_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    command_queue: asyncio.Queue = asyncio.Queue()
    planner_reply_queue: queue.Queue = queue.Queue()
    ask_human_queue: queue.Queue = queue.Queue()

    async def send_json(payload: dict):
        await websocket.send_json(payload)

    async def reader_loop():
        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get('type')
                if msg_type == 'planner_reply':
                    content = data.get('content', '')
                    planner_reply_queue.put(content)
                    sid = data.get('session_id') or state.SESSION_ID_DEMO
                    if sid not in state.SESSIONS:
                        state.SESSIONS[sid] = {
                            'history': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'planner_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    state.SESSIONS[sid]['history'].append(payload)
                    persistence._persist_history_event(sid, payload)
                    await send_json(payload)
                elif msg_type == 'ask_human_reply':
                    content = data.get('content', '')
                    ask_human_queue.put(content)
                    sid = data.get('session_id') or state.SESSION_ID_DEMO
                    if sid not in state.SESSIONS:
                        state.SESSIONS[sid] = {
                            'history': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'ask_human_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    state.SESSIONS[sid]['history'].append(payload)
                    persistence._persist_history_event(sid, payload)
                    await send_json(payload)
                elif msg_type == 'confirmation_reply':
                    content = data.get('content', '')
                    ask_human_queue.put(content)
                    sid = data.get('session_id') or state.SESSION_ID_DEMO
                    if sid not in state.SESSIONS:
                        state.SESSIONS[sid] = {
                            'history': [],
                            'last_task_id': None,
                        }
                    payload = {
                        'source': 'User',
                        'type': 'confirmation_reply',
                        'content': content,
                        'session_id': sid,
                    }
                    state.SESSIONS[sid]['history'].append(payload)
                    persistence._persist_history_event(sid, payload)
                    await send_json(payload)
                else:
                    await command_queue.put(data)
        except Exception:
            pass

    reader_task: asyncio.Task | None = None

    try:
        reader_task = asyncio.create_task(reader_loop())

        while True:
            data = await command_queue.get()

            if data.get('type') == 'cancel':
                sid = data.get('session_id')
                if sid:
                    if sid in state._run_stop_events:
                        state._run_stop_events[sid].set()
                    else:
                        state._pending_cancel.add(sid)
                await send_json(
                    {
                        'source': 'System',
                        'type': 'status',
                        'content': 'Cancelling...',
                        'session_id': sid,
                    }
                )
                continue

            user_prompt = (data.get('content') or '').strip()
            if not user_prompt:
                await send_json(
                    {
                        'source': 'System',
                        'type': 'status',
                        'content': 'Empty prompt ignored.',
                        'session_id': data.get('session_id'),
                    }
                )
                continue

            mode = (data.get('mode') or 'direct').strip().lower() or 'direct'
            if mode not in ('direct', 'planner'):
                mode = 'direct'

            session_id = data.get('session_id') or str(uuid.uuid4())
            if session_id not in state.SESSIONS:
                state.SESSIONS[session_id] = {
                    'history': [],
                    'last_task_id': None,
                }
            task_id = state.SESSIONS[session_id].get('last_task_id') or session_id
            state.SESSIONS[session_id]['last_task_id'] = task_id
            persistence._persist_meta(session_id, state.SESSIONS[session_id])
            user_msg = {
                'source': 'User',
                'type': 'query',
                'content': user_prompt,
                'mode': mode,
                'session_id': session_id,
            }
            state.SESSIONS[session_id]['history'].append(user_msg)
            persistence._persist_history_event(session_id, user_msg)
            await send_json(user_msg)

            await send_json(
                {
                    'source': 'System',
                    'type': 'status',
                    'content': f"Initializing ({mode})...",
                    'session_id': session_id,
                }
            )

            stop_ev = threading.Event()
            state._run_stop_events[session_id] = stop_ev
            if session_id in state._pending_cancel:
                stop_ev.set()
                state._pending_cancel.discard(session_id)
            bak = (data.get('bohrium_access_key') or '').strip() or None
            bpid = data.get('bohrium_project_id')
            if not bak and os.environ.get('ENABLE_SSH_SANDBOX', '').lower() in (
                '1',
                'true',
                'yes',
            ):
                bak = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip() or None
                bpid = bpid or os.environ.get('BOHRIUM_PROJECT_ID')
            if bpid is not None:
                try:
                    bpid = int(bpid)
                except (TypeError, ValueError):
                    bpid = None
            asyncio.get_event_loop().run_in_executor(
                state._executor,
                _run_agent_sync,
                session_id,
                user_prompt,
                send_json,
                loop,
                stop_ev,
                mode,
                planner_reply_queue,
                task_id,
                ask_human_queue,
                bak,
                bpid,
            )
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send_json({'source': 'System', 'type': 'error', 'content': str(e)})
        except Exception:
            pass
    finally:
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
