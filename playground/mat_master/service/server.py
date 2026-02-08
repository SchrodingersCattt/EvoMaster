"""
MatMaster web service: FastAPI + WebSocket for streaming agent runs.
Run from project root or playground/mat_master/service with PYTHONPATH including project root.
"""

import asyncio
import importlib
import queue
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Ensure project root is on path (service is at playground/mat_master/service)
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Register mat_master playground (same as run.py auto_import_playgrounds), so get_playground_class returns MatMasterPlayground
importlib.import_module("playground.mat_master.core.playground")

from evomaster.utils.types import TaskInstance

app = FastAPI(title="MatMaster Web Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, dict] = {}
SESSION_ID_DEMO = "demo_session"
_executor = ThreadPoolExecutor(max_workers=2)
# Per-connection cancel: set by WS "cancel", read by agent thread
_current_stop_event: threading.Event | None = None


class ChatRequest(BaseModel):
    prompt: str
    workspace: str = "./workspace"


@app.get("/", response_class=HTMLResponse)
def root():
    """Minimal dashboard (no need to run Next.js). Full UI: http://localhost:3000 when frontend is running."""
    return _DASHBOARD_HTML


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MatMaster</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 1rem; background: #e5e7eb; color: #1f2937; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #1e293b; }
    .bar { display: flex; gap: 0.5rem; margin-bottom: 0.5rem; align-items: center; flex-wrap: wrap; }
    .bar input { flex: 1; min-width: 200px; padding: 0.5rem 0.75rem; border: 1px solid #d1d5db; border-radius: 6px; background: #f9fafb; color: #1f2937; }
    .bar button { padding: 0.5rem 1rem; border: none; border-radius: 6px; cursor: pointer; }
    .bar .btn-send { background: #1e40af; color: #fff; }
    .bar .btn-cancel { background: #b91c1c; color: #fff; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .status { font-size: 0.875rem; color: #6b7280; margin-bottom: 0.5rem; }
    .grid { display: grid; grid-template-columns: 1fr 320px; gap: 1rem; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .logs { display: flex; flex-direction: column; gap: 0.75rem; max-height: 55vh; overflow-y: auto; }
    .log { border: 1px solid #d1d5db; border-radius: 8px; padding: 0.75rem 1rem; background: #f3f4f6; }
    .log.MatMaster { border-color: #1e40af; background: #eff6ff; }
    .log.Planner { border-color: #1e3a8a; background: #eff6ff; }
    .log.Coder { border-color: #1e40af; background: #eff6ff; }
    .log.ToolExecutor { border-color: #b91c1c; background: #fef2f2; }
    .log.System { border-color: #9ca3af; background: #f9fafb; }
    .log .src { font-size: 0.75rem; font-weight: 600; margin-bottom: 0.25rem; opacity: 0.9; }
    .log pre { margin: 0; font-size: 0.8125rem; white-space: pre-wrap; word-break: break-word; }
    .panel { background: #f3f4f6; border: 1px solid #d1d5db; border-radius: 8px; padding: 0.75rem; }
    .panel h2 { font-size: 0.9375rem; margin: 0 0 0.5rem 0; color: #1e293b; }
    .panel select { width: 100%; padding: 0.35rem; margin-bottom: 0.5rem; background: #fff; color: #1f2937; border: 1px solid #d1d5db; border-radius: 4px; }
    .file-list { max-height: 40vh; overflow-y: auto; font-size: 0.8125rem; }
    .file-list div { padding: 0.25rem 0.5rem; cursor: pointer; border-radius: 4px; }
    .file-list div:hover { background: #e5e7eb; }
    .file-list .dir { color: #1e40af; }
    .file-list .bread { margin-bottom: 0.5rem; color: #6b7280; font-size: 0.75rem; }
    .mode-label { font-size: 0.875rem; color: #6b7280; }
    .mode-select { padding: 0.35rem 0.5rem; border-radius: 6px; background: #f9fafb; color: #1f2937; border: 1px solid #d1d5db; }
    .planner-ask { margin: 0.75rem 0; padding: 1rem; border: 1px solid #1e40af; border-radius: 8px; background: #eff6ff; }
    .planner-ask .prompt { margin-bottom: 0.75rem; font-size: 0.9375rem; color: #1f2937; }
    .planner-ask .actions { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    .planner-ask input { flex: 1; min-width: 120px; padding: 0.4rem 0.6rem; border-radius: 6px; background: #fff; border: 1px solid #d1d5db; color: #1f2937; }
    .planner-ask button { padding: 0.4rem 0.75rem; border-radius: 6px; border: none; cursor: pointer; }
    .planner-ask .btn-go { background: #1e40af; color: #fff; }
    .planner-ask .btn-abort { background: #b91c1c; color: #fff; }
  </style>
</head>
<body>
  <h1>MatMaster</h1>
  <p class="status" id="status">连接中…</p>
  <div id="plannerAsk" class="planner-ask" style="display:none;">
    <div class="prompt" id="plannerPrompt"></div>
    <div class="actions">
      <input id="plannerInput" placeholder="输入 go 执行 / abort 放弃 / 或输入修改意见" />
      <button type="button" class="btn-go" id="plannerGo">Go</button>
      <button type="button" class="btn-abort" id="plannerAbort">Abort</button>
    </div>
  </div>
  <div class="bar">
    <label class="mode-label">Mode</label>
    <select id="modeSelect" class="mode-select">
      <option value="direct">Direct</option>
      <option value="planner">Planner</option>
    </select>
    <input id="input" placeholder="输入任务描述…" />
    <button id="btn" class="btn-send" disabled>发送</button>
    <button id="btnCancel" class="btn-cancel" disabled>终止</button>
  </div>
  <div class="grid">
    <div>
      <div class="logs" id="logs"></div>
    </div>
    <div class="panel">
      <h2>Runs / 文件</h2>
      <select id="runSelect"><option value="dev">dev (current)</option></select>
      <div class="file-list bread" id="bread"></div>
      <div class="file-list" id="fileList"></div>
    </div>
  </div>
  <script>
    const host = location.host;
    const api = location.origin;
    const wsUrl = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + host + '/ws/chat';
    const input = document.getElementById('input');
    const btn = document.getElementById('btn');
    const btnCancel = document.getElementById('btnCancel');
    const logs = document.getElementById('logs');
    const status = document.getElementById('status');
    const runSelect = document.getElementById('runSelect');
    const bread = document.getElementById('bread');
    const fileList = document.getElementById('fileList');
    const modeSelect = document.getElementById('modeSelect');
    let ws = null;
    let running = false;
    function addLog(msg) {
      const el = document.createElement('div');
      el.className = 'log ' + (msg.source || '');
      var text = typeof msg.content === 'object' ? JSON.stringify(msg.content, null, 2) : (msg.content != null ? String(msg.content) : '');
      if (msg.type === 'thought' && text.trim() === '') text = '(无文本输出)';
      el.innerHTML = '<div class="src">' + (msg.source || '') + '</div><pre>' + text.replace(/</g, '&lt;') + '</pre>';
      logs.appendChild(el);
      logs.scrollTop = logs.scrollHeight;
    }
    function connect() {
      ws = new WebSocket(wsUrl);
      ws.onopen = function() { status.textContent = '已连接'; btn.disabled = false; loadRuns(); };
      ws.onclose = function() { status.textContent = '已断开'; btn.disabled = true; btnCancel.disabled = true; };
      ws.onmessage = function(ev) {
        try {
          var msg = JSON.parse(ev.data);
          addLog(msg);
          if (msg.type === 'planner_ask') {
            document.getElementById('plannerPrompt').textContent = msg.content || '';
            document.getElementById('plannerAsk').style.display = 'block';
            document.getElementById('plannerInput').value = '';
          } else {
            document.getElementById('plannerAsk').style.display = 'none';
          }
          if (msg.type === 'finish' || msg.type === 'error' || msg.type === 'cancelled') running = false;
          btn.disabled = !ws || ws.readyState !== 1 || running;
          btnCancel.disabled = !running;
        } catch(e) {}
      };
    }
    btn.onclick = function() {
      var content = (input.value || '').trim();
      if (!content || !ws || ws.readyState !== 1 || running) return;
      running = true;
      btn.disabled = true;
      btnCancel.disabled = false;
      ws.send(JSON.stringify({ content: content, mode: modeSelect.value }));
      input.value = '';
    };
    btnCancel.onclick = function() {
      if (!ws || ws.readyState !== 1 || !running) return;
      ws.send(JSON.stringify({ type: 'cancel' }));
    };
    function sendPlannerReply(txt) {
      if (!ws || ws.readyState !== 1) return;
      ws.send(JSON.stringify({ type: 'planner_reply', content: (txt || '').trim() || 'abort' }));
      document.getElementById('plannerAsk').style.display = 'none';
    }
    document.getElementById('plannerGo').onclick = function() { sendPlannerReply('go'); };
    document.getElementById('plannerAbort').onclick = function() { sendPlannerReply('abort'); };
    document.getElementById('plannerInput').onkeydown = function(e) {
      if (e.key === 'Enter') sendPlannerReply(document.getElementById('plannerInput').value);
    };
    input.onkeydown = function(e) { if (e.key === 'Enter') btn.click(); };

    function loadRuns() {
      fetch(api + '/api/runs').then(r => r.json()).then(function(d) {
        runSelect.innerHTML = d.runs.map(function(r) { return '<option value="' + r.id + '">' + r.label + '</option>'; }).join('');
        loadFiles(runSelect.value, '');
      }).catch(function() {});
    }
    var currentPath = '';
    function loadFiles(runId, path) {
      currentPath = path;
      var url = api + '/api/runs/' + encodeURIComponent(runId) + '/files' + (path ? '?path=' + encodeURIComponent(path) : '');
      fetch(url).then(r => r.json()).then(function(d) {
        bread.textContent = (runId === 'dev' ? 'dev' : runId) + (path ? ' / ' + path : '');
        fileList.innerHTML = '';
        if (path) {
          var up = document.createElement('div');
          up.className = 'dir';
          up.textContent = '..';
          up.onclick = function() {
            var parts = path.split(/[/\\\\]/).filter(Boolean);
            parts.pop();
            loadFiles(runId, parts.join('/'));
          };
          fileList.appendChild(up);
        }
        d.entries.forEach(function(e) {
          var div = document.createElement('div');
          div.className = e.dir ? 'dir' : '';
          div.textContent = e.dir ? e.name + '/' : e.name;
          div.onclick = function() {
            if (e.dir) loadFiles(runId, e.path || e.name);
          };
          fileList.appendChild(div);
        });
      }).catch(function() { fileList.innerHTML = '<div>加载失败</div>'; });
    }
    runSelect.onchange = function() { loadFiles(runSelect.value, ''); };
    connect();
  </script>
</body>
</html>
"""


@app.get("/info")
def info():
    """Service info and links (for API users)."""
    return {
        "service": "MatMaster Web Service",
        "dashboard": "http://localhost:3000",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "ws_chat": "ws://localhost:8000/ws/chat",
        "api_start": "/api/start",
        "api_share": "/api/share/{session_id}",
    }


@app.post("/api/start")
async def start_task(req: ChatRequest):
    """Optional: signal ready and return session id."""
    if SESSION_ID_DEMO not in SESSIONS:
        SESSIONS[SESSION_ID_DEMO] = {"history": []}
    return {"status": "ready", "session_id": SESSION_ID_DEMO}


@app.get("/api/share/{session_id}")
def get_share_data(session_id: str):
    """Return session history for read-only share view."""
    data = SESSIONS.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data["history"]


def _runs_dir() -> Path:
    return _project_root / "runs"


def _get_run_workspace_path(run_id: str) -> Path | None:
    """Resolve run_id to workspace directory (same layout as run.py: runs/<run_id>/workspace or workspaces/<task>)."""
    runs = _runs_dir()
    run_path = runs / run_id
    if not run_path.is_dir():
        return None
    # Prefer run_dir/workspace, else run_dir/workspaces/task_0 or first task
    ws = run_path / "workspace"
    if ws.is_dir():
        return ws
    workspaces = run_path / "workspaces"
    if workspaces.is_dir():
        subs = [p for p in workspaces.iterdir() if p.is_dir()]
        if subs:
            return subs[0]
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
def list_run_files(run_id: str, path: str = ""):
    """List files under a run's workspace. path is optional subdir (relative)."""
    base = _get_run_workspace_path(run_id)
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
    user_prompt: str,
    send_cb,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
    mode: str = "direct",
    planner_reply_queue: queue.Queue | None = None,
):
    """Run MatMaster in a thread (direct or planner exp); send_cb streams events."""
    import logging
    logging.basicConfig(level=logging.INFO)

    def event_callback(source: str, event_type: str, content) -> None:
        payload = {"source": source, "type": event_type, "content": content}
        if SESSION_ID_DEMO not in SESSIONS:
            SESSIONS[SESSION_ID_DEMO] = {"history": []}
        SESSIONS[SESSION_ID_DEMO]["history"].append(payload)
        future = asyncio.run_coroutine_threadsafe(send_cb(payload), loop)
        try:
            future.result(timeout=5)
        except Exception:
            pass

    try:
        from evomaster.core import get_playground_class

        # 与 run.py 一致：config_path 和 run_dir 布局
        config_path = _project_root / "configs" / "mat_master" / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        pg = get_playground_class("mat_master", config_path=config_path)

        run_dir = _project_root / "runs" / "mat_master_web"
        task_id = "ws_" + uuid.uuid4().hex[:8]
        pg.set_run_dir(run_dir, task_id=task_id)
        pg.setup()

        mode = (mode or "direct").strip().lower() or "direct"
        pg.set_mode(mode)

        if mode == "planner" and planner_reply_queue is not None:
            pg._planner_input_fn = lambda prompt: _planner_ask_and_wait(
                prompt, send_cb, loop, planner_reply_queue
            )

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

        exp.run(task_description=user_prompt, task_id=task_id)
        if stop_event.is_set():
            event_callback("System", "cancelled", "Task cancelled by user.")
    except Exception as e:
        event_callback("System", "error", str(e))
        raise


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    global _current_stop_event
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
                    planner_reply_queue.put(data.get("content", ""))
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
                if _current_stop_event:
                    _current_stop_event.set()
                await send_json(
                    {"source": "System", "type": "status", "content": "Cancelling..."}
                )
                continue

            user_prompt = (data.get("content") or "").strip()
            if not user_prompt:
                await send_json(
                    {"source": "System", "type": "status", "content": "Empty prompt ignored."}
                )
                continue

            mode = (data.get("mode") or "direct").strip().lower() or "direct"
            if mode not in ("direct", "planner"):
                mode = "direct"

            await send_json(
                {"source": "System", "type": "status", "content": f"Initializing ({mode})..."}
            )

            _current_stop_event = threading.Event()
            await asyncio.get_event_loop().run_in_executor(
                _executor,
                _run_agent_sync,
                user_prompt,
                send_json,
                loop,
                _current_stop_event,
                mode,
                planner_reply_queue,
            )
            _current_stop_event = None

            await send_json(
                {"source": "System", "type": "finish", "content": "Done"}
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
        _current_stop_event = None
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    import os
    import uvicorn
    # Windows 上 reload 会 spawn 子进程，易触发 DuplicateHandle PermissionError，默认关闭
    force_reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")
    use_reload = force_reload or (sys.platform != "win32")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=use_reload,
    )
