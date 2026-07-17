#!/usr/bin/env python3
"""Replay an EvoMaster trajectory on the real MatMaster frontend and capture screenshots.

End-to-end pipeline:
1. Convert trajectory.json → history.jsonl (WebSocket event format)
2. Place into .state/<session_id>/ for the backend to load
3. Start FastAPI backend (replay-only mode, no agent/MCP init)
4. Start Next.js frontend
5. Wait for both services to be ready
6. Use Playwright to navigate to /share/<session_id> and capture screenshots
7. Shutdown services

Usage:
    python -m playground.mat_master.cli.replay_and_screenshot \
        --trajectory path/to/trajectory.json \
        --session-id hero_si_pivot \
        --output-dir ./screenshots/ \
        [--backend-port 50001] \
        [--frontend-port 3000] \
        [--width 1440] \
        [--scale 2] \
        [--theme light|dark] \
        [--full-page] \
        [--sections]

    # Or directly from the project root:
    cd agent/EvoMaster
    python playground/mat_master/cli/replay_and_screenshot.py \
        --trajectory ../walkthrough/hero_si_pivot/run_01/trajectories/task_0/trajectory.json \
        --session-id hero_si_pivot \
        --output-dir ../walkthrough/inner_loop_screenshots/real_outputs/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Resolve project paths
_THIS_FILE = Path(__file__).resolve()
_CLI_DIR = _THIS_FILE.parent
_MAT_MASTER_DIR = _CLI_DIR.parent  # playground/mat_master/
_PROJECT_ROOT = _MAT_MASTER_DIR.parent.parent  # EvoMaster/
_FRONTEND_DIR = _MAT_MASTER_DIR / "frontend"
_RUNS_DIR = _PROJECT_ROOT / "runs"


def _convert_trajectory(trajectory_path: Path, session_id: str, run_dir: Path) -> int:
    """Convert trajectory.json and install into .state/."""
    from playground.mat_master.cli.trajectory_to_history import convert

    events = convert(trajectory_path, session_id)

    state_dir = run_dir / ".state" / session_id
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write history.jsonl
    history_path = state_dir / "history.jsonl"
    with open(history_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # Write meta.json
    meta = {"last_task_id": "task_0"}
    (state_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  ✓ Converted {len(events)} events → {history_path}")
    return len(events)


def _wait_for_service(url: str, timeout: float = 60, label: str = "service") -> bool:
    """Poll a URL until it returns 200 or timeout."""
    import urllib.request
    import urllib.error

    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(1)
    print(f"  ✗ {label} did not become ready within {timeout}s", file=sys.stderr)
    return False


def _start_backend(run_dir: Path, port: int) -> subprocess.Popen:
    """Start uvicorn in replay-only mode."""
    env = os.environ.copy()
    env["MAT_MASTER_REPLAY_ONLY"] = "1"
    env["MAT_MASTER_RUN_DIR"] = str(run_dir)
    env.setdefault("PYTHONPATH", str(_PROJECT_ROOT))
    if str(_PROJECT_ROOT) not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = str(_PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "playground.mat_master.service.server:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def _start_frontend(port: int, api_port: int) -> subprocess.Popen:
    """Start Next.js dev server."""
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_URL"] = f"http://127.0.0.1:{api_port}"
    env["NEXT_PUBLIC_WS_URL"] = f"ws://127.0.0.1:{api_port}/ws/chat"
    env["NODE_ENV"] = "development"

    cmd = ["npm", "run", "dev", "--", "-H", "127.0.0.1", "-p", str(port)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_FRONTEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


async def _capture_screenshots(
    session_id: str,
    output_dir: Path,
    frontend_port: int,
    width: int,
    scale: int,
    theme: str,
    full_page: bool,
    sections: bool,
):
    """Use Playwright to capture screenshots of the share page."""
    from playwright.async_api import async_playwright

    output_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{frontend_port}/share/{session_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": width, "height": 900},
            device_scale_factor=scale,
            color_scheme="light" if theme == "light" else "dark",
        )
        page = await context.new_page()

        print(f"  Navigating to {url}")
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2000)  # Allow React hydration + rendering

        # If light theme, remove dark class from html
        if theme == "light":
            await page.evaluate("document.documentElement.classList.remove('dark')")
            await page.wait_for_timeout(500)

        # Full page screenshot
        if full_page:
            out_path = output_dir / f"{session_id}_full.png"
            await page.screenshot(path=str(out_path), full_page=True)
            size_kb = out_path.stat().st_size // 1024
            print(f"  ✓ {out_path.name} ({size_kb} KB)")

        # Viewport screenshot (first screen)
        viewport_path = output_dir / f"{session_id}_viewport.png"
        await page.screenshot(path=str(viewport_path), full_page=False)
        size_kb = viewport_path.stat().st_size // 1024
        print(f"  ✓ {viewport_path.name} ({size_kb} KB)")

        # Section screenshots: scroll to different parts of the conversation
        if sections:
            # Get total page height
            total_height = await page.evaluate("document.body.scrollHeight")
            viewport_height = 900

            # Capture ~7 evenly spaced sections
            num_sections = min(7, max(3, total_height // viewport_height))
            for i in range(num_sections):
                y_offset = int(i * (total_height - viewport_height) / max(1, num_sections - 1))
                await page.evaluate(f"window.scrollTo(0, {y_offset})")
                await page.wait_for_timeout(300)

                section_path = output_dir / f"{session_id}_section_{i+1:02d}.png"
                await page.screenshot(path=str(section_path), full_page=False)
                size_kb = section_path.stat().st_size // 1024
                print(f"  ✓ {section_path.name} ({size_kb} KB, y={y_offset})")

        await browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Replay trajectory on real MatMaster frontend and capture screenshots"
    )
    parser.add_argument("--trajectory", "-t", required=True, help="Path to trajectory.json")
    parser.add_argument("--session-id", "-s", required=True, help="Session ID for replay")
    parser.add_argument("--output-dir", "-o", required=True, help="Output directory for PNGs")
    parser.add_argument("--backend-port", type=int, default=50001, help="Backend port (default 50001)")
    parser.add_argument("--frontend-port", type=int, default=3000, help="Frontend port (default 3000)")
    parser.add_argument("--width", type=int, default=1440, help="Viewport width (default 1440)")
    parser.add_argument("--scale", type=int, default=2, help="Device scale factor (default 2)")
    parser.add_argument("--theme", choices=["light", "dark"], default="dark", help="Color theme")
    parser.add_argument("--full-page", action="store_true", default=True, help="Capture full page (default)")
    parser.add_argument("--no-full-page", action="store_false", dest="full_page")
    parser.add_argument("--sections", action="store_true", default=True, help="Capture sections (default)")
    parser.add_argument("--no-sections", action="store_false", dest="sections")
    parser.add_argument("--skip-convert", action="store_true", help="Skip conversion (history.jsonl already exists)")
    parser.add_argument("--run-dir", help="Override run directory")
    args = parser.parse_args()

    trajectory_path = Path(args.trajectory).resolve()
    output_dir = Path(args.output_dir).resolve()

    # Determine run directory
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    else:
        run_dir = _RUNS_DIR / "mat_master_web"
    run_dir.mkdir(parents=True, exist_ok=True)

    procs: list[subprocess.Popen] = []

    def cleanup():
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                except Exception:
                    pass

    def on_signal(signum, frame):
        cleanup()
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        # Phase 1: Convert trajectory
        if not args.skip_convert:
            print(f"\n[1/4] Converting trajectory → history.jsonl")
            if not trajectory_path.exists():
                print(f"  ERROR: {trajectory_path} not found", file=sys.stderr)
                sys.exit(1)
            _convert_trajectory(trajectory_path, args.session_id, run_dir)
        else:
            print(f"\n[1/4] Skipping conversion (--skip-convert)")

        # Phase 2: Start backend
        print(f"\n[2/4] Starting backend (replay-only) on port {args.backend_port}")
        p_backend = _start_backend(run_dir, args.backend_port)
        procs.append(p_backend)

        backend_url = f"http://127.0.0.1:{args.backend_port}/"
        if not _wait_for_service(backend_url, timeout=30, label="backend"):
            # Try reading stderr for error info
            stderr = p_backend.stderr.read(4096).decode() if p_backend.stderr else ""
            print(f"  Backend stderr: {stderr[:500]}", file=sys.stderr)
            cleanup()
            sys.exit(1)
        print(f"  ✓ Backend ready at {backend_url}")

        # Verify session is loaded
        import urllib.request
        share_url = f"http://127.0.0.1:{args.backend_port}/api/share/{args.session_id}"
        try:
            with urllib.request.urlopen(share_url, timeout=5) as resp:
                data = json.loads(resp.read())
                print(f"  ✓ Session '{args.session_id}' loaded: {len(data)} events")
        except Exception as e:
            print(f"  ✗ Session not accessible: {e}", file=sys.stderr)
            cleanup()
            sys.exit(1)

        # Phase 3: Start frontend
        print(f"\n[3/4] Starting frontend (Next.js) on port {args.frontend_port}")
        p_frontend = _start_frontend(args.frontend_port, args.backend_port)
        procs.append(p_frontend)

        frontend_url = f"http://127.0.0.1:{args.frontend_port}/"
        if not _wait_for_service(frontend_url, timeout=90, label="frontend"):
            stderr = p_frontend.stderr.read(4096).decode() if p_frontend.stderr else ""
            print(f"  Frontend stderr: {stderr[:500]}", file=sys.stderr)
            cleanup()
            sys.exit(1)
        print(f"  ✓ Frontend ready at {frontend_url}")

        # Phase 4: Playwright screenshots
        print(f"\n[4/4] Capturing screenshots → {output_dir}")
        asyncio.run(_capture_screenshots(
            session_id=args.session_id,
            output_dir=output_dir,
            frontend_port=args.frontend_port,
            width=args.width,
            scale=args.scale,
            theme=args.theme,
            full_page=args.full_page,
            sections=args.sections,
        ))

        print(f"\n✅ Done! Screenshots in: {output_dir}")

    finally:
        cleanup()


if __name__ == "__main__":
    main()
