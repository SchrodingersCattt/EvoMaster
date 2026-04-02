"""CLI entry point for mm-devshell."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from dotenv import load_dotenv

if TYPE_CHECKING:
    pass


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_agents_general_llm(main_config: Path) -> str | None:
    """``agents.general.llm`` from ``matmaster_config/config.yaml`` (profile key)."""
    if not main_config.is_file():
        return None
    with open(main_config, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("agents") or {}
    general = agents.get("general") or {}
    if isinstance(general, dict):
        v = general.get("llm")
        return str(v).strip() if v else None
    return None


def _normalize_argv(argv: list[str] | None) -> list[str]:
    """Prepend ``repl`` when omitted so ``mm-devshell --workdir ...`` keeps working."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return ["repl"]
    if argv[0] in ("repl", "run"):
        return argv
    if argv[0] in ("-h", "--help"):
        return ["repl"] + argv
    return ["repl"] + argv


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--workdir",
        type=Path,
        required=True,
        help="Workspace directory (persistent)",
    )
    common.add_argument(
        "--log-dir",
        type=Path,
        required=True,
        help="Event log directory",
    )
    common.add_argument(
        "--exp",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            "matmaster/exps/{NAME}.toml. Omit or ``devshell``: load ``direct`` but narrow "
            "skills_root to struct-DB + mcp-mat-sg lazymcp stubs; mat_sg tools narrowed to "
            "build_surface_slab. ``direct``: unpatched production toml. "
            "MCP paths use [skills].config_dir (typically matmaster_config/)."
        ),
    )
    common.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "LLM route key, matching routes in matmaster_config/llm_config.yaml "
            "(e.g. claude-sonnet-4-6); omit to use config.yaml agents.general.llm or llm_config default"
        ),
    )
    common.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session type override: local/docker/ssh",
    )
    common.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output",
    )

    parser = argparse.ArgumentParser(
        prog="mm-devshell",
        description="MatMaster DevShell -- matmaster agent (REPL or single run).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  mm-devshell --workdir ./ws --log-dir ./logs\n"
            "  mm-devshell repl --workdir ./ws --log-dir ./logs\n"
            "  mm-devshell run --workdir ./ws --log-dir ./logs -p \"Hello\"\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "repl",
        parents=[common],
        help="Interactive REPL (default when the first argument is omitted)",
        description="Start interactive REPL.",
    )

    run_p = sub.add_parser(
        "run",
        parents=[common],
        help="Run one prompt and exit (scripts / CI)",
        description="Run a single task non-interactively; prints one JSON line to stdout.",
    )
    run_g = run_p.add_mutually_exclusive_group(required=True)
    run_g.add_argument(
        "--prompt",
        "-p",
        type=str,
        metavar="TEXT",
        help="User prompt text",
    )
    run_g.add_argument(
        "--prompt-file",
        type=Path,
        metavar="PATH",
        help="Read prompt from file (UTF-8)",
    )
    run_p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write the same JSON line to this file",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments (after ``repl`` default injection)."""
    argv = _normalize_argv(argv)
    return build_parser().parse_args(argv)


def _bootstrap_runner(args: argparse.Namespace) -> tuple[Any, Any, Any, Any]:
    """Load LLM config, build provider, return DevRunner and related objects."""
    root = _project_root()
    llm_yaml = root / "matmaster_config" / "llm_config.yaml"
    main_yaml = root / "matmaster_config" / "config.yaml"

    if not llm_yaml.is_file():
        print(
            f"Error: LLM config not found: {llm_yaml}",
            file=sys.stderr,
        )
        sys.exit(1)

    from matmaster.config.loader import load_llm_config
    from matmaster.providers.llm_factory import build_provider

    llm_config = load_llm_config(llm_yaml)
    agent_default_llm = _load_agents_general_llm(main_yaml)

    model_override = (args.model or "").strip() or None

    try:
        llm_provider = build_provider(
            llm_config,
            model_override=model_override,
            default_profile_key=agent_default_llm,
        )
        resolved = llm_config.resolve_route(
            model_override=model_override,
            default_key=agent_default_llm,
        )
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Load .env files (same as main app in src/utils/constant.py)
    import os

    from dotenv import find_dotenv, load_dotenv

    load_dotenv()
    current_env = os.getenv("SERVICE_ENV", "test")
    load_dotenv(find_dotenv(f".env.{current_env}"))

    from matmaster.config.loader import load_exp_config
    from matmaster.devshell.config import DevConfig
    from matmaster.devshell.exp_patch import devshell_default_exp_config

    exp_opt = (getattr(args, "exp", None) or "").strip() or None
    try:
        if not exp_opt or exp_opt == "devshell":
            exp_override = devshell_default_exp_config()
        else:
            exp_override = load_exp_config(exp_opt)
    except (FileNotFoundError, ValueError) as e:
        label = exp_opt or "devshell"
        print(f"Error loading exp '{label}': {e}", file=sys.stderr)
        sys.exit(1)
    config = DevConfig()

    if args.session:
        config = config.model_copy(
            update={"session": config.session.model_copy(update={"type": args.session})}
        )

    args.workdir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    from matmaster.devshell.runner import DevRunner
    from matmaster.devshell.stream_hook import DevStreamHook

    # Suppress stream output in headless+json mode
    if (
        getattr(args, "prompt", None) is not None
        or getattr(args, "prompt_file", None) is not None
    ):
        stream_hook = DevStreamHook(verbose=args.verbose)
    else:
        stream_hook = DevStreamHook(verbose=args.verbose)

    runner = DevRunner(
        config=config,
        workdir=args.workdir,
        llm_provider=llm_provider,
        llm_config=llm_config,
        resolved_route=resolved,
        stream_hook=stream_hook,
        exp_config=exp_override,
    )
    return runner, config, llm_config, resolved


def _run_with_event_log(runner: Any, prompt: str, log_dir: Path) -> tuple[Any, Path]:
    """Run one task with MessageBus + EventLogger (same JSONL shape as REPL).

    Writes ``log_dir/events_YYYYMMDD_HHMMSS.jsonl``.
    """
    from matmaster.core.bus import MessageBus
    from matmaster.devshell.event_logger import EventLogger

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    event_logger = EventLogger(log_file, run_id="run-001")
    bus = MessageBus()

    result_holder: list[Any] = []
    error_holder: list[BaseException] = []

    def _worker() -> None:
        try:
            r = runner.run(prompt, bus=bus)
            result_holder.append(r)
        except BaseException as e:
            error_holder.append(e)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while worker.is_alive():
        try:
            event = bus.get_nowait()
            event_logger.log_event(event)
        except asyncio.QueueEmpty:
            time.sleep(0.1)
            continue

    while True:
        try:
            event = bus.get_nowait()
            event_logger.log_event(event)
        except asyncio.QueueEmpty:
            break

    worker.join()

    try:
        if error_holder:
            raise error_holder[0]
        if not result_holder:
            raise RuntimeError("run produced no result")
        result = result_holder[0]
        event_logger.log_event(result.result.to_run_result_event())
        return result, log_file
    finally:
        event_logger.close()


def _run_single(
    args: argparse.Namespace,
    runner: Any,
    resolved: Any,
) -> int:
    """Execute one prompt; print JSON line to stdout; optional --json-out."""
    if getattr(args, "prompt", None) is not None:
        prompt = args.prompt
    else:
        pf = args.prompt_file
        if not pf.is_file():
            print(f"Error: prompt file not found: {pf}", file=sys.stderr)
            return 1
        prompt = pf.read_text(encoding="utf-8")

    prompt = prompt.strip()
    if not prompt:
        print("Error: empty prompt", file=sys.stderr)
        return 1

    try:
        result, log_file = _run_with_event_log(runner, prompt, args.log_dir)
        if args.verbose:
            print(f"Event log: {log_file}", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    kr = result.result
    summary: dict[str, Any] = {
        "model": getattr(resolved, "model", None),
        "profile_key": getattr(resolved, "profile_key", None),
        "route_key": getattr(resolved, "route_key", None),
        "status": kr.status,
        "reason": kr.reason,
        "final_content": kr.final_content,
        "num_turns": kr.num_turns,
        "usage": dict(kr.usage) if kr.usage else {},
    }
    line = json.dumps(summary, ensure_ascii=False)
    print(line)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(line + "\n", encoding="utf-8")

    return 0 if kr.reason == "natural" else 1


def main(argv: list[str] | None = None) -> None:
    """Entry point for mm-devshell."""
    load_dotenv()
    args = parse_args(argv)

    if args.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            force=True,
        )

    runner, config, _llm_config, resolved = _bootstrap_runner(args)

    if args.command == "run":
        rc = _run_single(args, runner, resolved)
        raise SystemExit(rc)

    from matmaster.devshell.repl import run_repl

    run_repl(runner, config, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
