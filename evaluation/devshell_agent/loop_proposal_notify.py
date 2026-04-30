"""DevShell 编排循环：子 Agent 写出 proposal 文件时的飞书提醒（供 ``loop.py`` 调用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evaluation.devshell_agent.feishu_round_notify import (
    notify_manual_review_proposal_async,
)
from evaluation.devshell_agent.path_policy import (
    PROPOSED_MATMASTER_EXPS_CHANGES_NAME,
    PROPOSED_OPTIMIZATION_CHANGES_NAME,
    PROPOSED_QUESTION_BANK_CHANGES_NAME,
)


def notify_proposed_question_bank_if_present(
    *,
    session_dir: Path,
    iteration_index: int,
    checklist_reports: list[dict[str, Any]],
) -> None:
    path = session_dir / PROPOSED_QUESTION_BANK_CHANGES_NAME
    rationale = ""
    if checklist_reports:
        rationale = str(checklist_reports[-1].get("rationale") or "")
    notify_manual_review_proposal_async(
        kind="question_bank",
        session_dir=session_dir,
        iteration_index=iteration_index,
        proposal_path=path,
        report_text=rationale,
    )


def notify_proposed_matmaster_exps_if_present(
    *,
    session_dir: Path,
    iteration_index: int,
    delegation: dict[str, Any],
    optimization_reports: list[dict[str, Any]],
) -> None:
    path = session_dir / PROPOSED_MATMASTER_EXPS_CHANGES_NAME
    summary = ""
    if optimization_reports:
        summary = str(optimization_reports[-1].get("summary") or "")
    rnd_raw = delegation.get("optimization_round")
    rnd_i = int(rnd_raw) if rnd_raw is not None else None
    notify_manual_review_proposal_async(
        kind="matmaster_exps",
        session_dir=session_dir,
        iteration_index=iteration_index,
        proposal_path=path,
        report_text=summary,
        optimization_round=rnd_i,
    )


def notify_proposed_optimization_if_present(
    *,
    session_dir: Path,
    iteration_index: int,
    delegation: dict[str, Any],
    optimization_reports: list[dict[str, Any]],
) -> None:
    path = session_dir / PROPOSED_OPTIMIZATION_CHANGES_NAME
    summary = ""
    if optimization_reports:
        summary = str(optimization_reports[-1].get("summary") or "")
    rnd_raw = delegation.get("optimization_round")
    rnd_i = int(rnd_raw) if rnd_raw is not None else None
    notify_manual_review_proposal_async(
        kind="optimization",
        session_dir=session_dir,
        iteration_index=iteration_index,
        proposal_path=path,
        report_text=summary,
        optimization_round=rnd_i,
    )
