"""
Process manager for matmaster run: starts backend (uvicorn) and frontend (Next.js)
with MAT_MASTER_RUN_DIR and API/WS URLs set. Does not depend on start_dev.sh.
"""

import atexit
import os
import signal
import subprocess
import sys
from pathlib import Path

# Project root: cli/ -> mat_master/ -> playground/ -> EvoMaster
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent


def get_project_root() -> Path:
    return _PROJECT_ROOT


def _is_windows() -> bool:
    return sys.platform in ("win32", "cygwin") or os.environ.get("MSYSTEM")


def _release_port(port: int) -> None:
    """Kill any process listening on the given port (best-effort)."""
    try:
        if _is_windows():
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode != 0:
                return
            for line in out.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line.upper():
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit():
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True,
                                timeout=5,
                            )
                            print(f"  -> Released port {port} (PID {pid})", flush=True)
                            return
            print(f"  -> Port {port} was free", flush=True)
        else:
            # Prefer lsof then fuser
            out = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                pids = out.stdout.strip().split()
                for pid in pids:
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=3)
                    except Exception:
                        pass
                print(f"  -> Released port {port}", flush=True)
                return
            out = subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True,
                timeout=5,
            )
            if out.returncode == 0:
                print(f"  -> Released port {port}", flush=True)
                return
            print(f"  -> Port {port} was free", flush=True)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        print(f"  -> Port {port} (could not check/release)", flush=True)


def _get_public_host() -> str:
    if os.environ.get("PUBLIC_HOST", "").strip():
        return os.environ.get("PUBLIC_HOST", "").strip()
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        host = s.getsockname()[0]
        s.close()
        return host
    except Exception:
        return "127.0.0.1"


def run(
    work_dir: Path,
    *,
    backend_port: int,
    frontend_port: int,
    public_host: str | None = None,
) -> int:
    """
    Start backend and frontend with work_dir as MAT_MASTER_RUN_DIR.
    Blocks until one of the processes exits; then kills both and returns exit code.
    """
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    project_root = get_project_root()
    backend_port = int(backend_port)
    frontend_port = int(frontend_port)
    host = (public_host or _get_public_host()).strip()

    def out(msg: str) -> None:
        print(msg, flush=True)

    out("MatMaster: work_dir=%s" % work_dir)
    out("Releasing ports %s, %s (if in use)..." % (backend_port, frontend_port))
    _release_port(backend_port)
    _release_port(frontend_port)
    try:
        import time
        time.sleep(1)
    except Exception:
        pass

    env = os.environ.copy()
    env["MAT_MASTER_RUN_DIR"] = str(work_dir)
    env["NEXT_PUBLIC_API_URL"] = f"http://{host}:{backend_port}"
    env["NEXT_PUBLIC_WS_URL"] = f"ws://{host}:{backend_port}/ws/chat"

    python = sys.executable
    venv_python = project_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        python = str(venv_python)
    env.setdefault("PYTHONPATH", str(project_root))
    if "PYTHONPATH" in os.environ and str(project_root) not in env["PYTHONPATH"].split(os.pathsep):
        env["PYTHONPATH"] = str(project_root) + os.pathsep + env["PYTHONPATH"]

    backend_cmd = [
        python, "-m", "uvicorn",
        "playground.mat_master.service.server:app",
        "--host", "0.0.0.0",
        "--port", str(backend_port),
        "--reload",
    ]
    frontend_dir = project_root / "playground" / "mat_master" / "frontend"
    frontend_cmd = ["npm", "run", "dev", "--", "-H", "0.0.0.0", "-p", str(frontend_port)]

    procs: list[subprocess.Popen] = []

    def kill_all() -> None:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                except Exception:
                    pass

    def on_signal(signum: int, frame: object) -> None:
        kill_all()
        sys.exit(128 + (signum if signum is not None else 0))

    atexit.register(kill_all)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    out("Starting backend (FastAPI) on 0.0.0.0:%s... (loading tools may take a moment)" % backend_port)
    p_backend = subprocess.Popen(
        backend_cmd,
        cwd=str(project_root),
        env=env,
        stdout=None,
        stderr=None,
    )
    procs.append(p_backend)

    out("Starting frontend (Next.js) on 0.0.0.0:%s..." % frontend_port)
    p_frontend = subprocess.Popen(
        frontend_cmd,
        cwd=str(frontend_dir),
        env=env,
        shell=sys.platform == "win32",
        stdout=None,
        stderr=None,
    )
    procs.append(p_frontend)

    out("")
    out("========================================================================")
    out("  MatMaster running")
    out("  Work dir : %s" % work_dir)
    out("  Dashboard: http://%s:%s" % (host, frontend_port))
    out("  Backend  : http://%s:%s" % (host, backend_port))
    out("  Press Ctrl+C to stop")
    out("========================================================================")
    out("")

    try:
        # Wait for first process to exit
        while True:
            for p in procs:
                ret = p.poll()
                if ret is not None:
                    kill_all()
                    return ret
            try:
                import time
                time.sleep(0.5)
            except KeyboardInterrupt:
                kill_all()
                return 0
    except Exception:
        kill_all()
        return 1
