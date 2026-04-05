"""Snapshot v5 question ``id`` values under ``evaluation/question_bank/``."""

from __future__ import annotations

from pathlib import Path

from evaluation.core.runner import load_question_banks


def collect_question_bank_question_ids(repo_root: Path) -> frozenset[str]:
    """Return the set of all top-level question ``id`` strings across v5 bank YAML."""
    bank_dir = (repo_root / "evaluation" / "question_bank").resolve()
    banks = load_question_banks(bank_dir)
    ids: set[str] = set()
    for bank in banks:
        for q in bank.questions:
            ids.add(q.id)
    return frozenset(ids)
