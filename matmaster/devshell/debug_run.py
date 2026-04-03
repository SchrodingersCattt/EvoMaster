"""Debug entry point for devshell -- set breakpoints anywhere, F5 to run.

Usage:
    1. Edit PROMPT below (or pass via command line: python debug_run.py "your prompt")
    2. Set breakpoints in runner.py, kernel, tools, etc.
    3. F5 in VSCode (or run directly: python -m matmaster.devshell.debug_run)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

# -- Config --
PROMPT = "Build the nitroprusside anion. Export as `nitroprusside.xyz`. In the final answer, explicitly report: (1) whether `nitroprusside.xyz` exists; (2) the formula, written as `FeC5N6O`; (3) Fe coordination number; (4) the counts of Fe-C bonds shorter than 2.0 A and Fe-N bonds shorter than 2.0 A; (5) one representative N-O bond length and Fe-N-O angle; (6) one representative C-N bond length and Fe-C-N angle."  # <-- change this freely
WORKDIR = Path(__file__).resolve().parent.parent.parent / "debug_workspace"
LOG_DIR = WORKDIR / "logs"
LLM_CONFIG: Path | None = None  # None = auto-detect config/llm_config.yaml
MODEL_OVERRIDE: str | None = "claude-opus-4-6"  # e.g. "claude-sonnet-4-6"
CONFIG_FILE: Path | None = None  # None = use DevConfig defaults
VERBOSE = True
# --


def main(prompt: str | None = None) -> None:
    import os

    from dotenv import find_dotenv, load_dotenv

    load_dotenv()
    current_env = os.getenv("SERVICE_ENV", "test")
    load_dotenv(find_dotenv(f".env.{current_env}"))

    from matmaster.devshell.config import DevConfig, load_dev_config

    # Config
    if CONFIG_FILE:
        config = load_dev_config(CONFIG_FILE)
    else:
        config = DevConfig()

    # Dirs
    WORKDIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # LLM provider (same logic as cli._bootstrap_runner)
    from matmaster.config.loader import load_llm_config
    from matmaster.devshell.cli import _load_agents_general_llm, _project_root
    from matmaster.providers.llm_factory import build_provider

    root = _project_root()
    llm_yaml = LLM_CONFIG or (root / "config" / "llm_config.yaml")
    main_yaml = root / "config" / "config.yaml"

    llm_config = load_llm_config(llm_yaml)
    agent_default_llm = _load_agents_general_llm(main_yaml)
    llm_provider = build_provider(
        llm_config,
        model_override=MODEL_OVERRIDE,
        default_profile_key=agent_default_llm,
    )
    resolved = llm_config.resolve_route(
        model_override=MODEL_OVERRIDE,
        default_key=agent_default_llm,
    )

    # Runner
    from matmaster.devshell.event_observer import DevEventObserver
    from matmaster.devshell.runner import DevRunner
    from matmaster.devshell.stream_hook import DevStreamHook

    stream_hook = DevStreamHook(verbose=VERBOSE)
    runner = DevRunner(
        config=config,
        workdir=WORKDIR,
        llm_provider=llm_provider,
        llm_config=llm_config,
        resolved_route=resolved,
        stream_hook=stream_hook,
    )

    task = prompt or PROMPT
    observer = DevEventObserver()
    stop_event = threading.Event()

    # -- Breakpoint-friendly: step into runner.run() --
    result = runner.run(task, stop_event=stop_event, event_observer=observer)

    # Print summary
    kr = result.result
    print(f"\n{'='*60}")
    print(f"Status: {kr.status} | Reason: {kr.reason} | Turns: {kr.num_turns}")
    print(f"Usage: {kr.usage}")
    if kr.final_content:
        print(f"\n--- Final Content ---\n{kr.final_content}")


if __name__ == "__main__":
    # Allow prompt from command line: python debug_run.py "your prompt"
    cli_prompt = sys.argv[1] if len(sys.argv) > 1 else None
    main(cli_prompt)
