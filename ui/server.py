"""EvoMaster UI 后端（解耦）

启动后用户输入 task 指令，前端通过 SSE 接收运行事件，按模块展示：当前在做什么、是否成功/报错。
"""

import importlib
import logging
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))

# 自动导入 playground 以触发注册
def _auto_import_playgrounds():
    playground_dir = PROJECT_ROOT / "playground"
    if not playground_dir.exists():
        return
    for agent_dir in playground_dir.iterdir():
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"playground.{agent_dir.name}.core.playground")
        except ImportError:
            pass

_auto_import_playgrounds()

from evomaster.core import get_playground_class, list_registered_playgrounds
from run import run_single_task

logger = logging.getLogger(__name__)

app = FastAPI(title="EvoMaster UI", description="任务运行与事件流")

# run_id -> { "queue": Queue, "result": None, "done": False }
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()


class RunRequest(BaseModel):
    task: str
    agent: str = "minimal"
    config: str | None = None


@app.get("/api/agents")
def api_agents():
    """列出已注册的 playground（agent）列表"""
    try:
        names = list_registered_playgrounds()
        return {"agents": names}
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/run")
def api_run(req: RunRequest):
    """提交一次运行，返回 run_id；前端用此 id 拉取 SSE 事件流"""
    config_path = Path(req.config) if req.config else (PROJECT_ROOT / "configs" / req.agent / "config.yaml")
    if not config_path.exists():
        raise HTTPException(status_code=400, detail=f"配置文件不存在: {config_path}")

    run_id = str(uuid.uuid4())
    run_dir = PROJECT_ROOT / "runs" / f"ui_{req.agent}_{run_id[:8]}"
    event_queue = queue.Queue()

    with _runs_lock:
        _runs[run_id] = {"queue": event_queue, "result": None, "done": False}

    def worker():
        try:
            result = run_single_task(
                agent_name=req.agent,
                config_path=config_path,
                run_dir=run_dir,
                task_id="task_0",
                task_description=req.task.strip(),
                event_sink=event_queue,
            )
            # 只把可序列化的摘要传给前端（不含 trajectory 等大对象）
            detail = {
                "status": result.get("status"),
                "steps": result.get("steps", 0),
                "task_id": result.get("task_id"),
                "run_dir": str(run_dir),
            }
            if "error" in result:
                detail["error"] = result["error"]
            event_queue.put({"phase": "done", "message": "运行结束", "status": "success", "detail": detail})
        except Exception as e:
            logger.exception(e)
            event_queue.put({"phase": "error", "message": str(e), "status": "failed", "detail": {"error": str(e)}})
        finally:
            with _runs_lock:
                if run_id in _runs:
                    _runs[run_id]["done"] = True

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    return {"run_id": run_id, "run_dir": str(run_dir)}


def _sse_stream(run_id: str):
    with _runs_lock:
        if run_id not in _runs:
            yield f"data: {__import__('json').dumps({'error': 'run_id not found'})}\n\n"
            return
        event_queue = _runs[run_id]["queue"]
    while True:
        try:
            event = event_queue.get(timeout=30)
        except queue.Empty:
            with _runs_lock:
                if run_id in _runs and _runs[run_id]["done"]:
                    break
            yield f"data: {__import__('json').dumps({'heartbeat': True})}\n\n"
            continue
        if isinstance(event, dict) and event.get("phase") == "done":
            yield f"data: {__import__('json').dumps(event, default=str, ensure_ascii=False)}\n\n"
            break
        yield f"data: {__import__('json').dumps(event, default=str, ensure_ascii=False)}\n\n"


@app.get("/api/runs/{run_id}/stream")
def api_run_stream(run_id: str):
    """SSE 事件流：推送各阶段事件（config/session/tools/agent/exp_start/exp_step/exp_end/error/done）"""
    return StreamingResponse(
        _sse_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 静态页面
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    """返回 UI 单页"""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "EvoMaster UI", "docs": "/docs", "agents": "/api/agents"}
