"""CLI entry point for mm-devshell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="mm-devshell",
        description="MatMaster DevShell -- interactive agent testing CLI",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        required=True,
        help="Workspace directory (persistent)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        required=True,
        help="Event log directory",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional devshell YAML (agent/session/tools only; LLM 来自 matmaster_config/llm_config.yaml)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "LLM route key，与 matmaster_config/llm_config.yaml 中 routes 一致 "
            "(例: claude-sonnet-4-6)；省略则使用 config.yaml 里 agents.general.llm 或 llm_config 的 default"
        ),
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session type override: local/docker/ssh",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for mm-devshell."""
    load_dotenv()
    args = parse_args(argv)

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

    if args.session:
        config = config.model_copy(
            update={"session": config.session.model_copy(update={"type": args.session})}
        )

    args.workdir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    from matmaster.devshell.repl import run_repl
    from matmaster.devshell.runner import DevRunner
    from matmaster.devshell.stream_hook import DevStreamHook

    stream_hook = DevStreamHook(verbose=args.verbose)
    runner = DevRunner(
        config=config,
        workdir=args.workdir,
        llm_provider=llm_provider,
        llm_config=llm_config,
        resolved_route=resolved,
        stream_hook=stream_hook,
    )

    run_repl(runner, config, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
