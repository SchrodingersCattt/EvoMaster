"""CLI entry point for mm-devshell."""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from matmaster.config.loader import load_agents_general_llm

if TYPE_CHECKING:
    pass


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


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
            "matmaster/exps/{NAME}.toml. Omit --exp to load `direct` (same as production)."
        ),
    )
    common.add_argument(
        "--exclude-subagents",
        nargs="*",
        default=None,
        metavar="NAME",
        help="Subagent exp names to exclude from Agent tool (e.g. --exclude-subagents verification).",
    )
    common.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional devshell YAML (agent/session/tools only; LLM comes from config/llm_config.yaml)",
    )
    common.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "LLM route key, matching routes in config/llm_config.yaml "
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
            '  mm-devshell run --workdir ./ws --log-dir ./logs -p "Hello"\n'
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
    run_p.add_argument(
        "--inject-bohrium-failure",
        type=str,
        default=None,
        metavar="MSG",
        help="Patch BohriumTool._submit to always return this error (eval-only)",
    )
    run_p.add_argument(
        "--billing-mode",
        type=str,
        default=None,
        metavar="MODE",
        help=(
            "Enable per-call usage reporting to tools-server with this billing_mode "
            "(e.g. 'eval'; 'eval'/'byok' do not debit credits). Omit to disable."
        ),
    )
    run_p.add_argument(
        "--invocation-id",
        type=str,
        default=None,
        metavar="ID",
        help="Stable invocation id used to correlate billing usage/cost for this run.",
    )

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments (after ``repl`` default injection)."""
    argv = _normalize_argv(argv)
    return build_parser().parse_args(argv)


def _bootstrap_runner(args: argparse.Namespace) -> tuple[Any, Any, Any, Any, Any]:
    """Load LLM config, build provider, return DevRunner and related objects.

    Also returns the ``UsageCollectingProvider`` wrapper so callers read
    ``collected_calls`` directly instead of reflecting DevRunner internals.
    """
    import os

    root = _project_root()
    llm_yaml = root / "config" / "llm_config.yaml"
    main_yaml = root / "config" / "config.yaml"

    if not llm_yaml.is_file():
        print(
            f"Error: LLM config not found: {llm_yaml}",
            file=sys.stderr,
        )
        sys.exit(1)

    from matmaster.config.loader import load_llm_config
    from matmaster.providers.llm_factory import build_provider_bundle
    from matmaster.providers.usage_collector import UsageCollectingProvider

    llm_config = load_llm_config(llm_yaml)
    agent_default_llm = load_agents_general_llm(main_yaml)

    model_override = (args.model or "").strip() or None

    try:
        llm_bundle = build_provider_bundle(
            llm_config,
            model_override=model_override,
            default_profile_key=agent_default_llm,
        )
        llm_provider = llm_bundle.provider
        resolved = llm_config.resolve(
            model_override=model_override,
            default_key=agent_default_llm,
        )
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Collect per-call usage (root + subagent + compaction share this instance).
    # When --billing-mode is set, also report each call to tools-server and
    # back-fill per-call cost (eval/byok do not debit credits).
    reporter = None
    billing_mode = (getattr(args, "billing_mode", None) or "").strip() or None
    if billing_mode:
        from clients.billing import (
            BillingRunContext,
            BillingUsageReporter,
            get_billing_service,
        )

        invocation_id = (getattr(args, "invocation_id", None) or "").strip() or None
        reporter = BillingUsageReporter(
            billing_service=get_billing_service(),
            run_context=BillingRunContext(
                session_id=invocation_id or "eval",
                task_id=None,
                invocation_id=invocation_id,
            ),
            billing_mode=billing_mode,
        )
    llm_provider = UsageCollectingProvider(
        llm_provider,
        model=getattr(resolved, "model", "") or "",
        reporter=reporter,
    )

    # Load .env files (same as main app in src/utils/constant.py)
    from dotenv import find_dotenv, load_dotenv

    load_dotenv()
    current_env = os.getenv("SERVICE_ENV", "test")
    load_dotenv(find_dotenv(f".env.{current_env}"))

    from matmaster.config.loader import load_exp_config
    from matmaster.devshell.config import DevConfig, load_dev_config

    exp_opt = (getattr(args, "exp", None) or "").strip() or None
    exp_override = None
    if exp_opt is not None or not args.config:
        try:
            if not exp_opt:
                exp_override = load_exp_config("direct")
            else:
                exp_override = load_exp_config(exp_opt)
        except (FileNotFoundError, ValueError) as e:
            label = exp_opt or "direct"
            print(f"Error loading exp '{label}': {e}", file=sys.stderr)
            sys.exit(1)

    if args.config:
        try:
            config = load_dev_config(args.config)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            sys.exit(1)
    else:
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
    if args.command == "run":
        stream_hook = DevStreamHook(output=io.StringIO(), verbose=args.verbose)
    else:
        stream_hook = DevStreamHook(verbose=args.verbose)

    runner = DevRunner(
        config=config,
        workdir=args.workdir,
        llm_provider=llm_provider,
        llm_config=llm_config,
        resolved_route=resolved,
        llm_bundle=llm_bundle,
        stream_hook=stream_hook,
        exp_config=exp_override,
        exclude_subagents=getattr(args, "exclude_subagents", None),
        inject_bohrium_failure=getattr(args, "inject_bohrium_failure", None),
    )
    return runner, config, llm_config, resolved, llm_provider


def _run_with_event_log(runner: Any, prompt: str, log_dir: Path) -> tuple[Any, Path]:
    """Run one task with DevEventObserver + EventLogger (same JSONL shape as REPL).

    Writes ``log_dir/events_YYYYMMDD_HHMMSS.jsonl``.
    """
    from queue import Empty

    from matmaster.devshell.event_logger import EventLogger
    from matmaster.devshell.event_observer import DevEventObserver

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    event_logger = EventLogger(log_file, run_id="run-001")
    observer = DevEventObserver()

    result_holder: list[Any] = []
    error_holder: list[BaseException] = []

    def _worker() -> None:
        try:
            r = runner.run(prompt, event_observer=observer)
            result_holder.append(r)
        except BaseException as e:
            error_holder.append(e)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while worker.is_alive():
        try:
            event = observer.get_nowait()
            event_logger.log_event(event)
        except Empty:
            time.sleep(0.1)
            continue

    # Drain remaining events
    for event in observer.drain():
        event_logger.log_event(event)

    worker.join()

    try:
        if error_holder:
            raise error_holder[0]
        if not result_holder:
            raise RuntimeError("run produced no result")
        result = result_holder[0]
        return result, log_file
    finally:
        event_logger.close()


def _run_single(
    args: argparse.Namespace,
    runner: Any,
    resolved: Any,
    usage_provider: Any = None,
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

    summary: dict[str, Any] = {
        "model": getattr(getattr(resolved, "profile", None), "model", None),
        "profile_key": getattr(resolved, "profile_key", None),
        "route_key": getattr(resolved, "profile_key", None),
        "status": result.status,
        "reason": result.reason,
        "final_content": result.final_content,
        "num_turns": result.num_turns,
        "usage": dict(result.usage) if result.usage else {},
    }
    vendor_turns = getattr(result, "usage_vendor_by_turn", ())
    if vendor_turns:
        summary["usage_vendor_by_turn"] = [dict(item) for item in vendor_turns]
    collected = usage_provider.collected_calls if usage_provider is not None else None
    if collected:
        from matmaster.providers.usage_collector import per_call_usage_payload

        summary["per_call_usage"] = per_call_usage_payload(collected)
    finish_detail = getattr(result, "finish_detail", None)
    if finish_detail is not None:
        if hasattr(finish_detail, "model_dump"):
            summary["finish_detail"] = finish_detail.model_dump(mode="json")
        else:
            summary["finish_detail"] = dict(finish_detail)
    line = json.dumps(summary, ensure_ascii=False)
    print(line)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(line + "\n", encoding="utf-8")

    return 0 if result.reason == "natural" else 1


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

    runner, config, _llm_config, resolved, usage_provider = _bootstrap_runner(args)

    if args.command == "run":
        rc = _run_single(args, runner, resolved, usage_provider)
        raise SystemExit(rc)

    from matmaster.devshell.repl import run_repl

    run_repl(runner, config, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
