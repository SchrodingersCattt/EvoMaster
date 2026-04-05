#!/usr/bin/env python3
"""外层编排：Claude Agent SDK 驱动「DevShell 批量跑题 → 判分 → 改仓库」多轮迭代。

依赖（需单独安装）::

    uv sync --extra eval-agent

在仓库根执行示例::

    uv run python evaluation/scripts/devshell/run_devshell_agent_loop.py \\
      --max-iterations 3 --target-mean-score 80 --limit 2 --jobs 2 \\
      --questions SC_struct_007

说明见 ``evaluation/docs/devshell/devshell_agent_sdk_loop.md``。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# evaluation/scripts/devshell/this_file.py -> four parents to repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class DevshellAgentLoopCli:
    """Argparse + env loading + :class:`DevshellAgentLoop` entry."""

    default_repo_root = REPO_ROOT

    @staticmethod
    def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
        p = argparse.ArgumentParser(
            description=(
                "DevShell Agent SDK loop: run_devshell_eval → judge → edit repo "
                "(multi-iteration)."
            ),
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        p.add_argument(
            "--repo-root",
            type=Path,
            default=DevshellAgentLoopCli.default_repo_root,
            help="MatMaster repository root (cwd for SDK + inner eval).",
        )
        p.add_argument(
            "--session-dir",
            type=Path,
            default=None,
            help="Loop session directory (default: results/devshell_agent_loop_<UTC>).",
        )
        p.add_argument(
            "--clean-results",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Before starting, remove the repository <repo-root>/results directory "
                "if it exists (then recreated when writing the new session). "
                "Use --no-clean-results to keep prior runs."
            ),
        )
        p.add_argument(
            "--max-iterations",
            type=int,
            default=3,
            help="Maximum outer iterations (each is one SDK session turn).",
        )
        p.add_argument(
            "--target-mean-score",
            type=int,
            default=80,
            help="Stop early when macro mean reaches this (0–100) or model sets target_met.",
        )
        p.add_argument(
            "--permission-mode",
            type=str,
            default="acceptEdits",
            help="ClaudeAgentOptions.permission_mode (e.g. acceptEdits).",
        )
        p.add_argument(
            "--max-sdk-turns",
            type=int,
            default=100,
            help="Max SDK turns per iteration (ClaudeAgentOptions.max_turns).",
        )
        p.add_argument(
            "--extra-instruction",
            type=str,
            default="",
            help="Appended to each iteration user message (focus areas, constraints).",
        )
        p.add_argument(
            "--git-reset-on-regression",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "After each iteration, if macro mean is below the previous iteration's "
                "and git head was recorded at iteration start, run git reset --hard."
            ),
        )
        p.add_argument(
            "--eval-ingest-submit-each-iteration",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "When eval-ingest-pending-only is on, immediately after each "
                "run_devshell_eval completes, run score_devshell_tasks.py --submit "
                "for that output directory."
            ),
        )
        p.add_argument(
            "--eval-ingest-submit-timeout",
            type=float,
            default=120.0,
            help="HTTP timeout (seconds) per ingest POST during automatic --submit.",
        )
        p.add_argument(
            "--enable-checklist-agent",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "After each main iteration, run a second SDK session that may only "
                "edit evaluation/question_bank/ when the main agent called "
                "escalate_checklist_revision."
            ),
        )
        p.add_argument(
            "--checklist-permission-mode",
            type=str,
            default="",
            help="ClaudeAgentOptions.permission_mode for checklist agent (default: same as --permission-mode).",
        )

        p.add_argument(
            "--modes",
            nargs="+",
            default=["direct"],
            help="Forwarded to run_devshell_eval --modes",
        )
        p.add_argument(
            "--jobs",
            type=int,
            default=8,
            help=(
                "Single knob for: run_devshell_eval --jobs, score_devshell_tasks "
                "--score-jobs, and checklist agent max_turns (= jobs×2)."
            ),
        )
        p.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Forwarded to run_devshell_eval --limit (default: no cap).",
        )
        p.add_argument(
            "--questions",
            nargs="+",
            default=None,
            help="Forwarded to run_devshell_eval --questions",
        )
        p.add_argument(
            "--capabilities",
            nargs="+",
            default=None,
            help="Forwarded to run_devshell_eval --capabilities",
        )
        p.add_argument(
            "--model",
            type=str,
            default="claude-opus-4-6",
            help="Forwarded to run_devshell_eval --model (inner mm-devshell route).",
        )
        p.add_argument(
            "--exp",
            type=str,
            default=None,
            help="Forwarded to run_devshell_eval --exp",
        )
        p.add_argument(
            "--eval-config",
            type=Path,
            default=None,
            help="Forwarded to run_devshell_eval --eval-config",
        )
        p.add_argument(
            "--eval-ingest-pending-only",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Forwarded to run_devshell_eval (default: pending-only ingest payloads).",
        )
        p.add_argument(
            "--no-export-review",
            action="store_true",
            help="Forwarded to run_devshell_eval --no-export-review",
        )
        p.add_argument(
            "--task-timeout",
            type=float,
            default=1200.0,
            help="Forwarded to run_devshell_eval --task-timeout",
        )
        p.add_argument(
            "--eval-extra-arg",
            action="append",
            default=[],
            metavar="TOKEN",
            help="Extra argv token(s) appended to run_devshell_eval.py (repeatable).",
        )
        return p.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parse_args(argv)
        repo_root = args.repo_root.resolve()
        sys.path.insert(0, str(repo_root))

        from dotenv import find_dotenv, load_dotenv

        load_dotenv(repo_root / ".env")
        current_env = os.environ.get("SERVICE_ENV", "test")
        env_file = find_dotenv(f".env.{current_env}")
        if env_file:
            load_dotenv(env_file, override=True)

        from evaluation.devshell_agent.config_state import DevshellAgentCliDefaults
        from evaluation.devshell_agent.loop import AgentLoopConfig, DevshellAgentLoop

        results_root = repo_root / "results"
        if args.clean_results and results_root.exists():
            shutil.rmtree(results_root)
            print(f"Cleared results directory: {results_root}", file=sys.stderr)

        session_dir = args.session_dir
        if session_dir is None:
            session_dir = DevshellAgentLoop.default_session_dir(repo_root=repo_root)
        else:
            session_dir = (
                session_dir if session_dir.is_absolute() else (repo_root / session_dir)
            ).resolve()

        defaults = DevshellAgentCliDefaults(
            modes=list(args.modes),
            jobs=int(args.jobs),
            limit=args.limit,
            questions=list(args.questions) if args.questions else None,
            capabilities=list(args.capabilities) if args.capabilities else None,
            model=args.model,
            exp=args.exp,
            eval_ingest_pending_only=bool(args.eval_ingest_pending_only),
            no_export_review=bool(args.no_export_review),
            task_timeout_sec=float(args.task_timeout),
            eval_config=(
                args.eval_config.resolve()
                if args.eval_config is not None
                else repo_root / "evaluation" / "config.yaml"
            ),
            extra_args=list(args.eval_extra_arg),
        )

        cfg = AgentLoopConfig(
            repo_root=repo_root,
            session_dir=session_dir,
            defaults=defaults,
            max_iterations=max(1, int(args.max_iterations)),
            target_mean_score=max(0, min(100, int(args.target_mean_score))),
            permission_mode=str(args.permission_mode),
            max_sdk_turns=max(1, int(args.max_sdk_turns)),
            extra_instruction=str(args.extra_instruction or ""),
            git_reset_on_regression=bool(args.git_reset_on_regression),
            eval_ingest_submit_each_iteration=bool(
                args.eval_ingest_submit_each_iteration
            ),
            eval_ingest_submit_timeout=max(1.0, float(args.eval_ingest_submit_timeout)),
            enable_checklist_agent=bool(args.enable_checklist_agent),
            checklist_permission_mode=str(args.checklist_permission_mode or ""),
        )

        print(f"Session directory: {session_dir}", file=sys.stderr)
        return DevshellAgentLoop(cfg).run_sync()


def main(argv: list[str] | None = None) -> int:
    return DevshellAgentLoopCli().main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError as e:
        print(
            "Import error (install optional deps): uv sync --extra eval-agent",
            file=sys.stderr,
        )
        print(e, file=sys.stderr)
        raise SystemExit(2) from e
