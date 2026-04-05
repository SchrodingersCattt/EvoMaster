#!/usr/bin/env python3
"""Load ``ci/baseline_eval_preset.yaml`` for baseline-eval CI.

Used by ``generate_eval_child_pipeline.py`` and ``run_baseline_eval.sh`` (via CLI).

Resolution order:

* ``BASELINE_CHILD_PIPELINE`` env: ``capabilities`` | ``questions`` (overrides file).
* Else preset key ``child_pipeline`` (default ``capabilities``).

Question IDs (for ``questions`` layout):

* ``BASELINE_QUESTIONS`` env: comma-separated ids (overrides file list).
* Else preset key ``question_ids`` (list of strings).

Questions source mode (for ``questions`` layout; see ``run_baseline_eval.sh``):

* Preset key ``questions_mode``: ``preset`` | ``score_summary_missing_cc`` (默认 ``preset``)。

Eval runner:

* ``EVAL_RUNNER`` env: ``claude_cli`` | ``devshell`` (overrides file).
* Else preset key ``eval_runner`` (default ``devshell``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRESET_PATH = REPO_ROOT / "ci" / "baseline_eval_preset.yaml"
_VALID_RUNNERS = frozenset({"claude_cli", "devshell"})
_VALID_QUESTIONS_MODES = frozenset({"preset", "score_summary_missing_cc"})


def load_preset_file(path: Path | None = None) -> dict:
    p = path or DEFAULT_PRESET_PATH
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_child_pipeline() -> str:
    env = os.environ.get("BASELINE_CHILD_PIPELINE", "").strip().lower()
    if env in ("capabilities", "questions"):
        return env
    data = load_preset_file()
    cp = str(data.get("child_pipeline", "capabilities")).strip().lower()
    if cp in ("capabilities", "questions"):
        return cp
    return "capabilities"


def resolve_question_ids() -> list[str]:
    env_q = os.environ.get("BASELINE_QUESTIONS", "").strip()
    if env_q:
        return [x.strip() for x in env_q.split(",") if x.strip()]
    data = load_preset_file()
    raw = data.get("question_ids") or []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def resolve_questions_mode() -> str:
    """How to choose question ids when child_pipeline is ``questions``."""
    data = load_preset_file()
    raw = str(data.get("questions_mode", "preset")).strip().lower()
    if raw in _VALID_QUESTIONS_MODES:
        return raw
    return "preset"


def resolve_eval_runner() -> str:
    env = os.environ.get("EVAL_RUNNER", "").strip().lower()
    if env in _VALID_RUNNERS:
        return env
    data = load_preset_file()
    er = str(data.get("eval_runner", "devshell")).strip().lower()
    if er in _VALID_RUNNERS:
        return er
    return "devshell"


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: baseline_eval_preset.py "
            "list-ids|child-pipeline|eval-runner|questions-mode",
            file=sys.stderr,
        )
        return 2
    op = sys.argv[1]
    if op == "list-ids":
        for q in resolve_question_ids():
            print(q)
        return 0
    if op == "child-pipeline":
        print(resolve_child_pipeline())
        return 0
    if op == "eval-runner":
        print(resolve_eval_runner())
        return 0
    if op == "questions-mode":
        print(resolve_questions_mode())
        return 0
    print(f"unknown op: {op}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
