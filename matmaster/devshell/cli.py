"""CLI entry point for mm-devshell."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="mm-devshell",
        description="MatMaster DevShell -- interactive agent testing CLI",
    )
    parser.add_argument(
        "--workdir", type=Path, required=True,
        help="Workspace directory (persistent)",
    )
    parser.add_argument(
        "--log-dir", type=Path, required=True,
        help="Event log directory",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Config file path (default: use built-in defaults)",
    )
    parser.add_argument(
        "--session", type=str, default=None,
        help="Session type override: local/docker/ssh",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Enable verbose output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for mm-devshell."""
    args = parse_args(argv)

    from matmaster.devshell.config import DevConfig, load_dev_config

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

    # Override session type if specified on CLI
    if args.session:
        config = config.model_copy(
            update={"session": config.session.model_copy(update={"type": args.session})}
        )

    # Validate API key
    if not config.llm.api_key:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print(
                "Error: No API key. Set OPENAI_API_KEY env var or "
                "specify llm.api_key in config.",
                file=sys.stderr,
            )
            sys.exit(1)
        config = config.model_copy(
            update={"llm": config.llm.model_copy(update={"api_key": api_key})}
        )

    # Ensure directories exist
    args.workdir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    # Create LLM provider
    from matmaster.providers.openai_provider import OpenAIProvider

    llm_provider = OpenAIProvider(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
        stream_timeout=config.llm.stream_timeout,
        stream_idle_timeout=config.llm.stream_idle_timeout,
        max_retries=config.llm.max_retries,
        retry_delay=config.llm.retry_delay,
    )

    # Create stream hook and runner
    from matmaster.devshell.stream_hook import DevStreamHook
    from matmaster.devshell.runner import DevRunner
    from matmaster.devshell.repl import run_repl

    stream_hook = DevStreamHook(verbose=args.verbose)
    runner = DevRunner(
        config=config,
        workdir=args.workdir,
        llm_provider=llm_provider,
        stream_hook=stream_hook,
    )

    # Start REPL
    run_repl(runner, config, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
