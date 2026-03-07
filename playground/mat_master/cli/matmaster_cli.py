"""
matmaster CLI entry point. Usage: matmaster run <work_dir> [options]
"""

import argparse
import sys
from pathlib import Path

# Default backend port: 8000 on Windows (avoid WinError 10013), 50001 elsewhere
def _default_backend_port() -> int:
    if sys.platform in ("win32", "cygwin") or __import__("os").environ.get("MSYSTEM"):
        return 8000
    return 50001


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="matmaster",
        description="Run MatMaster playground (backend + frontend) with a custom work directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start backend and frontend with work_dir as run root")
    run_parser.add_argument(
        "work_dir",
        type=str,
        help="Working directory (logs, workspaces go here; same role as runs/mat_master_web)",
    )
    run_parser.add_argument(
        "--backend-port",
        type=int,
        default=None,
        help="Backend port (default: 8000 on Windows, 50001 elsewhere)",
    )
    run_parser.add_argument(
        "--frontend-port",
        type=int,
        default=50004,
        help="Frontend port (default: 50004)",
    )
    run_parser.add_argument(
        "--public-host",
        type=str,
        default=None,
        help="Public host/IP for API and WS URLs (default: auto-detect)",
    )

    args = parser.parse_args()
    if args.command != "run":
        parser.print_help()
        return 0

    work_dir = Path(args.work_dir).expanduser().resolve()
    backend_port = args.backend_port if args.backend_port is not None else _default_backend_port()

    from playground.mat_master.cli.launcher import run as launcher_run
    return launcher_run(
        work_dir,
        backend_port=backend_port,
        frontend_port=args.frontend_port,
        public_host=args.public_host,
    )


if __name__ == "__main__":
    sys.exit(main())
