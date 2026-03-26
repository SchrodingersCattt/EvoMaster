"""REPL loop for mm-devshell."""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from matmaster.devshell.config import DevConfig
from matmaster.devshell.event_logger import EventLogger
from matmaster.devshell.runner import DevRunner
from matmaster.core.bus import MessageBus


BUILTIN_COMMANDS = {"help", "config", "tools", "clear", "history", "verbose"}

HELP_TEXT = """\
Builtin commands:
  /help     Show this help
  /config   Show current configuration
  /tools    List registered tools
  /clear    Clear screen
  /history  Show conversation history summary
  /verbose  Toggle verbose mode

Ctrl+C    Cancel current run
Ctrl+D    Exit"""


def parse_command(text: str) -> tuple[str, str] | None:
    """Parse a /command from input. Returns (cmd, args) or None if not a command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return (cmd, args)


def format_banner(
    config: DevConfig, workdir: str, log_dir: str
) -> str:
    """Format the startup banner."""
    return (
        f"MatMaster Dev Shell v0.1\n"
        f"Model: {config.llm.model} | Session: {config.session.type} | "
        f"Tools: builtin\n"
        f"Workdir: {workdir} | Logs: {log_dir}\n"
        f"Type /help for commands, Ctrl+C to cancel current run, Ctrl+D to exit."
    )


def run_repl(
    runner: DevRunner,
    config: DevConfig,
    *,
    log_dir: Path,
    verbose: bool = False,
) -> None:
    """Main REPL loop."""
    run_counter = 0

    # One EventLogger per session
    log_file = log_dir / f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    event_logger = EventLogger(log_file, run_id="run-000")

    print(format_banner(config, str(runner._workdir), str(log_dir)))
    print()

    while True:
        try:
            user_input = input(">>> ")
        except EOFError:
            print("\nBye.")
            break
        except KeyboardInterrupt:
            print("\nUse Ctrl+D to exit.")
            continue

        if not user_input.strip():
            continue

        # Check for builtin command
        cmd_result = parse_command(user_input)
        if cmd_result is not None:
            cmd, args = cmd_result
            if cmd == "help":
                print(HELP_TEXT)
            elif cmd == "config":
                _show_config(config)
            elif cmd == "tools":
                _show_tools(runner)
            elif cmd == "clear":
                os.system("clear" if os.name != "nt" else "cls")
            elif cmd == "history":
                _show_history(runner)
            elif cmd == "verbose":
                verbose = not verbose
                runner._stream_hook._verbose = verbose
                print(f"Verbose mode: {'on' if verbose else 'off'}")
            else:
                print(f"Unknown command: /{cmd}. Type /help for available commands.")
            continue

        # Agent run
        run_counter += 1
        run_id = f"run-{run_counter:03d}"
        event_logger.set_run_id(run_id)

        bus = MessageBus()
        stop_event = threading.Event()

        original_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(signum: int, frame: Any) -> None:
            stop_event.set()
            print("\n\nCancelling...")

        signal.signal(signal.SIGINT, _sigint_handler)

        try:
            result_holder: list[Any] = []
            error_holder: list[Exception] = []

            def _worker() -> None:
                try:
                    result = runner.run(user_input, stop_event=stop_event, bus=bus)
                    result_holder.append(result)
                except Exception as e:
                    error_holder.append(e)

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            while worker.is_alive():
                try:
                    event = bus.get(timeout=0.1)
                    event_logger.log_event(event)
                except queue.Empty:
                    continue

            # Drain remaining events
            while True:
                try:
                    event = bus.get_nowait()
                    event_logger.log_event(event)
                except queue.Empty:
                    break

            worker.join()

            if error_holder:
                print(f"\nerror: {error_holder[0]}\n")
            elif result_holder:
                result = result_holder[0]
                event_logger.log_event(result.result.to_run_result_event())

        finally:
            signal.signal(signal.SIGINT, original_handler)

    event_logger.close()


def _show_config(config: DevConfig) -> None:
    """Display current configuration."""
    print(f"LLM: model={config.llm.model}, base_url={config.llm.base_url}")
    print(
        f"Agent: name={config.agent.name}, max_turns={config.agent.max_turns}"
    )
    print(f"Session: type={config.session.type}")
    print(f"Tools: builtin={config.tools.builtin}")
    if config.agent.identity:
        print(f"Identity: {config.agent.identity}")


def _show_tools(runner: DevRunner) -> None:
    """List registered tools."""
    from matmaster.core.exp import Exp

    exp = Exp(runner._exp_config)
    runtime = exp.build_runtime(runner._pg_ctx)
    try:
        registry = runtime.spec.tool_registry
        if registry and registry.all_tools:
            for tool in registry.all_tools:
                desc = getattr(tool, "description", "")
                name = getattr(tool, "name", str(tool))
                print(f"  - {name}: {desc}")
        else:
            print("  No tools registered.")
    finally:
        runtime.cleanup()


def _show_history(runner: DevRunner) -> None:
    """Show conversation history summary."""
    if not runner.history:
        print("No conversation history.")
        return
    print(f"History: {len(runner.history)} messages")
    for i, msg in enumerate(runner.history):
        role = msg.role if hasattr(msg, "role") else type(msg).__name__
        content = getattr(msg, "content", "") or ""
        preview = content[:80] + "..." if len(content) > 80 else content
        print(f"  [{i}] {role}: {preview}")
