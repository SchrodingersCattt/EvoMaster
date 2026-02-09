"""
MatMaster web service: FastAPI + WebSocket for streaming agent runs.
Run from project root or playground/mat_master/service with PYTHONPATH including project root.

Tools (MCP, skills, etc.) are loaded once at startup so the first user message does not wait.
"""

import asyncio
import importlib
import logging
import queue
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure project root is on path (service is at playground/mat_master/service)
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Register mat_master playground (same as run.py auto_import_playgrounds), so get_playground_class returns MatMasterPlayground
importlib.import_module("playground.mat_master.core.playground")

from evomaster.utils.types import TaskInstance

logger = logging.getLogger(__name__)

# Pre-initialized playground (tools loaded at startup). Reused per run with set_run_dir(task_id).
# Single worker so only one run at a time and run_dir is correct.
_cached_pg = None
_playground_init_done = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)


def _init_playground_sync() -> None:
    """Load playground once: config, LLM, session, MCP tools, skills, agent. Run at startup."""
    global _cached_pg
    try:
        from evomaster.core import get_playground_class

        config_path = _project_root / "configs" / "mat_master" / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class("mat_master", config_path=config_path)
        run_dir = _project_root / "runs" / RUN_ID_WEB
        run_dir.mkdir(parents=True, exist_ok=True)
        pg.set_run_dir(run_dir)
        pg.setup()
        _cached_pg = pg
        logger.info("Playground (tools, MCP, agent) initialized at startup.")
    except Exception as e:
        logger.exception("Playground init at startup failed: %s", e)
        _cached_pg = None
    finally:
        _playground_init_done.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load tools in a thread so server is ready only after tools are loaded."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_playground_sync)
    yield
    # shutdown: nothing to tear down for now


app = FastAPI(title="MatMaster Web Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, dict] = {}
SESSION_ID_DEMO = "demo_session"
RUN_ID_WEB = "mat_master_web"
# Per-session cancel: session_id -> Event, read by agent thread
_run_stop_events: dict[str, threading.Event] = {}


class ChatRequest(BaseModel):
    prompt: str
    workspace: str = "./workspace"


@app.get("/")
def root():
    """API only. Use http://localhost:3000 for the dashboard."""
    return {
        "service": "MatMaster Web Service",
        "message": "API only. Dashboard at http://localhost:3000",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "ws_chat": "/ws/chat",
    }


@app.get("/info")
def info():
    """Service info and links (for API users)."""
    return {
        "service": "MatMaster Web Service",
        "dashboard": "http://localhost:3000",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "ws_chat": "ws://localhost:50001/ws/chat",
        "api_start": "/api/start",
        "api_share": "/api/share/{session_id}",
    }


@app.post("/api/start")
async def start_task(req: ChatRequest):
    """Optional: signal ready and return session id."""
    if SESSION_ID_DEMO not in SESSIONS:
        SESSIONS[SESSION_ID_DEMO] = {"history": [], "task_ids": [], "last_task_id": None}
    return {"status": "ready", "session_id": SESSION_ID_DEMO}


@app.get("/api/sessions")
def list_sessions():
    """List session ids (for session picker)."""
    return {"sessions": [{"id": sid, "history_length": len(data.get("history", []))} for sid, data in SESSIONS.items()]}


@app.get("/api/sessions/{session_id}/history")
def get_session_history(session_id: str):
    """Return session history (for loading when switching session)."""
    data = SESSIONS.get(session_id)
    if not data:
        return []
    return data.get("history", [])


@app.get("/api/sessions/{session_id}/run_info")
def get_session_run_info(session_id: str):
    """Return run_id and task_ids for this session (for files/logs tied to current session)."""
    data = SESSIONS.get(session_id)
    if not data:
        return {"run_id": RUN_ID_WEB, "last_task_id": None, "task_ids": []}
    task_ids = data.get("task_ids") or []
    last_task_id = data.get("last_task_id")
    return {"run_id": RUN_ID_WEB, "last_task_id": last_task_id, "task_ids": task_ids}


@app.get("/api/sessions/{session_id}/files")
def list_session_files(session_id: str, path: str = ""):
    """List files under this session's workspace.

    Always uses runs/mat_master_web/workspaces/<key>/ (never the single run_dir/workspace):
    - If the session has a last run: key = last_task_id (e.g. ws_abc123).
    - If no run yet: key = session_id (e.g. demo_session), folder created on first access.
    """
    runs = _runs_dir()
    run_path = runs / RUN_ID_WEB
    if not run_path.is_dir():
        return {"run_id": RUN_ID_WEB, "path": path or ".", "entries": [], "workspace_root": None, "task_id": None}
    data = SESSIONS.get(session_id)
    task_id = (data or {}).get("last_task_id") if data else None
    if task_id is None:
        task_id = session_id
        (run_path / "workspaces" / task_id).mkdir(parents=True, exist_ok=True)
    base = _get_run_workspace_path(RUN_ID_WEB, task_id=task_id)
    if not base or not base.is_dir():
        return {"run_id": RUN_ID_WEB, "path": path or ".", "entries": [], "workspace_root": None, "task_id": task_id}
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside workspace")
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append({
            "name": p.name,
            "path": str(rel).replace("\\", "/"),
            "dir": p.is_dir(),
        })
    return {
        "run_id": RUN_ID_WEB,
        "path": path or ".",
        "entries": entries,
        "workspace_root": str(base) if base else None,
        "task_id": task_id,
    }


@app.get("/api/share/{session_id}")
def get_share_data(session_id: str):
    """Return session history for read-only share view."""
    data = SESSIONS.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data["history"]


def _runs_dir() -> Path:
    return _project_root / "runs"


def _get_run_workspace_path(run_id: str, task_id: str | None = None) -> Path | None:
    """Resolve run_id (and optional task_id) to workspace directory."""
    runs = _runs_dir()
    run_path = runs / run_id
    if not run_path.is_dir():
        return None
    if task_id:
        ws = run_path / "workspaces" / task_id
        if ws.is_dir():
            return ws
        return None
    ws = run_path / "workspace"
    if ws.is_dir():
        return ws
    workspaces = run_path / "workspaces"
    if workspaces.is_dir():
        subs = [p for p in workspaces.iterdir() if p.is_dir()]
        if subs:
            return max(subs, key=lambda p: p.stat().st_mtime)
    return run_path


@app.get("/api/runs")
def list_runs():
    """List run directories (mat_master_* under runs/, same as run.py)."""
    runs_list: list[dict] = []
    rd = _runs_dir()
    if rd.is_dir():
        for p in sorted(rd.iterdir(), key=lambda x: x.name, reverse=True):
            if p.is_dir() and p.name.startswith("mat_master_"):
                runs_list.append({"id": p.name, "label": p.name})
    if not runs_list:
        runs_list.append({"id": "mat_master_web", "label": "mat_master_web (created on first run)"})
    return {"runs": runs_list}


@app.get("/api/runs/{run_id}/files")
def list_run_files(run_id: str, path: str = "", task_id: str | None = None):
    """List files under a run's workspace. path is optional subdir; task_id pins to that workspace."""
    base = _get_run_workspace_path(run_id, task_id=task_id)
    if not base or not base.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    target = (base / path).resolve() if path else base
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside workspace")
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        rel = p.relative_to(base)
        entries.append({
            "name": p.name,
            "path": str(rel).replace("\\", "/"),
            "dir": p.is_dir(),
        })
    return {"run_id": run_id, "path": path or ".", "entries": entries}


def _planner_ask_and_wait(
    prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    reply_queue: queue.Queue,
) -> str:
    """Send planner_ask to client and block until planner_reply is put in reply_queue."""
    payload = {"source": "Planner", "type": "planner_ask", "content": prompt}
    future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
    try:
        future.result(timeout=5)
    except Exception:
        pass
    try:
        return reply_queue.get(timeout=300)
    except queue.Empty:
        return "abort"


def _run_agent_sync(
    session_id: str,
    user_prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
    mode: str = "direct",
    planner_reply_queue: queue.Queue | None = None,
    task_id: str | None = None,
):
    """Run MatMaster in a thread (direct or planner exp); send_cb streams events. task_id is set by caller."""
    import logging
    logging.basicConfig(level=logging.INFO)
    run_done: threading.Event | None = None

    def event_callback(source: str, event_type: str, content) -> None:
        payload = {"source": source, "type": event_type, "content": content, "session_id": session_id}
        if session_id not in SESSIONS:
            SESSIONS[session_id] = {"history": [], "task_ids": [], "last_task_id": None}
        if event_type != "log_line":
            SESSIONS[session_id]["history"].append(payload)
        future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

    try:
        _playground_init_done.wait(timeout=300)
        run_dir = _project_root / "runs" / RUN_ID_WEB
        task_id = task_id or ("ws_" + uuid.uuid4().hex[:8])

        if _cached_pg is not None:
            pg = _cached_pg
            pg.set_run_dir(run_dir, task_id=task_id)
        else:
            from evomaster.core import get_playground_class

            config_path = _project_root / "configs" / "mat_master" / "config.yaml"
            if not config_path.exists():
                raise FileNotFoundError(f"Config not found: {config_path}")
            pg = get_playground_class("mat_master", config_path=config_path)
            pg.set_run_dir(run_dir, task_id=task_id)
            pg.setup()

        run_done = threading.Event()

        mode = (mode or "direct").strip().lower() or "direct"
        pg.set_mode(mode)

        if mode == "planner" and planner_reply_queue is not None:
            pg._planner_input_fn = lambda prompt: _planner_ask_and_wait(
                prompt, send_cb, loop, planner_reply_queue
            )
        # Planner 的 LLM 输出（方案 JSON、Plan Report、步骤列表）通过 event_callback 推送到前端
        pg._planner_output_callback = event_callback

        base = pg.agent
        config_dict = pg.config.model_dump()
        agents_block = config_dict.get("agents")
        if isinstance(agents_block, dict) and agents_block:
            agent_config = next(iter(agents_block.values()))
        else:
            agent_config = config_dict.get("agent") or {}
        if not isinstance(agent_config, dict):
            agent_config = {}
        system_prompt_file = agent_config.get("system_prompt_file")
        user_prompt_file = agent_config.get("user_prompt_file")
        playground_base = Path(str(pg.config_dir).replace("configs", "playground"))
        if system_prompt_file:
            p = Path(system_prompt_file)
            if not p.is_absolute():
                system_prompt_file = str((playground_base / p).resolve())
        if user_prompt_file:
            p = Path(user_prompt_file)
            if not p.is_absolute():
                user_prompt_file = str((playground_base / p).resolve())
        prompt_format_kwargs = agent_config.get("prompt_format_kwargs", {})

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
        agent.set_agent_name(getattr(base, "_agent_name", "default"))
        agent._stop_event = stop_event

        pg.agent = agent
        exp = pg._create_exp()
        exp.set_run_dir(run_dir)
        event_callback("MatMaster", "exp_run", exp.__class__.__name__)

        exp.run(task_description=user_prompt, task_id=task_id)
        if stop_event.is_set():
            event_callback("System", "cancelled", "Task cancelled by user.")
        else:
            event_callback("System", "finish", "Done")
    except Exception as e:
        event_callback("System", "error", str(e))
        raise
    finally:
        if run_done is not None:
            run_done.set()
        _run_stop_events.pop(session_id, None)


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    command_queue: asyncio.Queue = asyncio.Queue()
    planner_reply_queue: queue.Queue = queue.Queue()

    async def send_json(payload: dict):
        await websocket.send_json(payload)

    async def reader_loop():
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "planner_reply":
                    content = data.get("content", "")
                    planner_reply_queue.put(content)
                    sid = data.get("session_id") or SESSION_ID_DEMO
                    if sid not in SESSIONS:
                        SESSIONS[sid] = {"history": [], "task_ids": [], "last_task_id": None}
                    payload = {"source": "User", "type": "planner_reply", "content": content, "session_id": sid}
                    SESSIONS[sid]["history"].append(payload)
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

            if data.get("type") == "cancel":
                sid = data.get("session_id")
                if sid and sid in _run_stop_events:
                    _run_stop_events[sid].set()
                await send_json(
                    {"source": "System", "type": "status", "content": "Cancelling...", "session_id": sid}
                )
                continue

            user_prompt = (data.get("content") or "").strip()
            if not user_prompt:
                await send_json(
                    {"source": "System", "type": "status", "content": "Empty prompt ignored.", "session_id": data.get("session_id")}
                )
                continue

            mode = (data.get("mode") or "direct").strip().lower() or "direct"
            if mode not in ("direct", "planner"):
                mode = "direct"

            session_id = data.get("session_id") or str(uuid.uuid4())
            if session_id not in SESSIONS:
                SESSIONS[session_id] = {"history": [], "task_ids": [], "last_task_id": None}
            task_id = "ws_" + uuid.uuid4().hex[:8]
            SESSIONS[session_id].setdefault("task_ids", []).append(task_id)
            SESSIONS[session_id]["last_task_id"] = task_id
            user_msg = {"source": "User", "type": "query", "content": user_prompt, "mode": mode, "session_id": session_id}
            SESSIONS[session_id]["history"].append(user_msg)
            await send_json(user_msg)

            await send_json(
                {"source": "System", "type": "status", "content": f"Initializing ({mode})...", "session_id": session_id}
            )

            stop_ev = threading.Event()
            _run_stop_events[session_id] = stop_ev
            asyncio.get_event_loop().run_in_executor(
                _executor,
                _run_agent_sync,
                session_id,
                user_prompt,
                send_json,
                loop,
                stop_ev,
                mode,
                planner_reply_queue,
                task_id,
            )
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send_json(
                {"source": "System", "type": "error", "content": str(e)}
            )
        except Exception:
            pass
    finally:
        pass
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    import os
    import uvicorn
    # host="0.0.0.0" so backend is reachable from other machines (server deployment)
    # Windows 上 reload 会 spawn 子进程，易触发 DuplicateHandle PermissionError，默认关闭
    force_reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")
    use_reload = force_reload or (sys.platform != "win32")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=50001,
        reload=use_reload,
    )
