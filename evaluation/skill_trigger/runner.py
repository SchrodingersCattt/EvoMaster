"""Skill trigger-rate evaluation runner.

Sends test prompts through the matmaster Exp runtime and checks whether
the model calls the Skill tool with the expected skill name within max_turns.

Early-stop: as soon as the model emits a ToolCallEvent for the **target** Skill,
the run is cancelled via CancellationToken. Other (non-target) Skill calls are
recorded but do not stop the run — this supports multi-skill tasks where the
target skill may not be the first one triggered. Umbrella skills (e.g.
atomic-structure) are always passed through. If max_turns is exhausted without
the target Skill call, we record "not triggered".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from matmaster.bohrium.runtime import try_attach_local_bohrium_runtime_from_env
from matmaster.config.loader import load_exp_config, load_llm_config
from matmaster.core.exp import Exp
from matmaster.core.playground import PlaygroundContext
from matmaster.core.stream_drain import DrainResult
from matmaster.providers.llm_factory import build_provider
from matmaster.sessions.local import LocalSession
from matmaster.types.cancellation import CancellationController
from matmaster.types.events import ToolCallEvent
from matmaster.types.run_metadata import RunMetadata

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TURNS = 3
_DEFAULT_EXP_NAME = "direct"
_DEFAULT_REPEATS = 3


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RepeatResult:
    triggered_skills: list[str] = field(default_factory=list)
    non_skill_tools: list[str] = field(default_factory=list)
    duration_ms: int = 0
    turns_used: int = 0
    error: str | None = None

    @property
    def triggered_skill(self) -> str | None:
        """First skill triggered (backward compat)."""
        return self.triggered_skills[0] if self.triggered_skills else None

    @property
    def hit(self) -> bool:
        return len(self.triggered_skills) > 0


@dataclass
class CaseResult:
    skill: str
    prompt: str
    case_type: str  # "positive" | "negative"
    repeats: list[RepeatResult] = field(default_factory=list)
    verdict: str = ""  # "pass" | "fail"

    @property
    def trigger_rate(self) -> float:
        """Fraction of repeats that triggered the target skill (anywhere in max_turns)."""
        if not self.repeats:
            return 0.0
        hits = sum(1 for r in self.repeats if self.skill in r.triggered_skills)
        return hits / len(self.repeats)

    @property
    def any_triggered_target(self) -> bool:
        return any(self.skill in r.triggered_skills for r in self.repeats)

    @property
    def all_triggered_target(self) -> bool:
        return all(self.skill in r.triggered_skills for r in self.repeats)

    @property
    def none_triggered_target(self) -> bool:
        return not any(self.skill in r.triggered_skills for r in self.repeats)

    @property
    def total_duration_ms(self) -> int:
        return sum(r.duration_ms for r in self.repeats)


@dataclass
class SkillReport:
    skill: str
    positive_results: list[CaseResult] = field(default_factory=list)
    negative_results: list[CaseResult] = field(default_factory=list)

    @property
    def positive_pass_count(self) -> int:
        return sum(1 for r in self.positive_results if r.verdict == "pass")

    @property
    def negative_pass_count(self) -> int:
        return sum(1 for r in self.negative_results if r.verdict == "pass")

    @property
    def recall(self) -> float:
        if not self.positive_results:
            return 0.0
        return self.positive_pass_count / len(self.positive_results)

    @property
    def specificity(self) -> float:
        if not self.negative_results:
            return 0.0
        return self.negative_pass_count / len(self.negative_results)

    @property
    def mean_positive_trigger_rate(self) -> float:
        """Average per-case trigger rate across positive cases."""
        if not self.positive_results:
            return 0.0
        return sum(r.trigger_rate for r in self.positive_results) / len(
            self.positive_results
        )


# ---------------------------------------------------------------------------
# Early-stop event handler
# ---------------------------------------------------------------------------


class _SkillTriggerDetector:
    """Monitors stream events and stops when the target skill appears."""

    def __init__(self, cancel_ctrl: CancellationController, target_skill: str) -> None:
        self._cancel_ctrl = cancel_ctrl
        self._target_skill = target_skill
        self.triggered_skills: list[str] = []
        self.non_skill_tools: list[str] = []
        self.turns_seen: int = 0

    @property
    def target_hit(self) -> bool:
        return self._target_skill in self.triggered_skills

    def on_event(self, event: Any) -> None:
        if not isinstance(event, ToolCallEvent):
            return

        self.turns_seen += 1

        if event.tool_name == "Skill":
            skill = (
                event.arguments.get("skill") or event.arguments.get("skill_name") or ""
            ).lstrip("/")
            self.triggered_skills.append(skill)
            if skill == self._target_skill:
                self._cancel_ctrl.cancel()
        else:
            self.non_skill_tools.append(event.tool_name)


# ---------------------------------------------------------------------------
# Single case runner
# ---------------------------------------------------------------------------


async def _run_single_repeat(
    *,
    prompt: str,
    target_skill: str,
    case_type: str,
    max_turns: int,
    exp_name: str,
    mat_config_path: Path,
    workspace_root: Path,
    case_index: int,
    repeat_index: int,
    model_route: str | None = None,
) -> RepeatResult:
    """Run one prompt once through the Exp runtime with early-stop on Skill call."""

    result = RepeatResult()
    task_id = f"trigger_{target_skill}_{case_type}_{case_index:02d}_r{repeat_index}"

    workspace = workspace_root / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    cache_area = workspace / ".cache"
    cache_area.mkdir(parents=True, exist_ok=True)

    try:
        llm_config = load_llm_config(mat_config_path)
        llm_provider = build_provider(llm_config, model_override=model_route)
        exp_config = load_exp_config(exp_name)
        exp_config = exp_config.model_copy(update={"max_turns": max_turns})

        session = LocalSession(workspace_path=workspace)
        session.open()

        pg_ctx = PlaygroundContext(
            workdir=workspace,
            session_type="local",
            cache_area=cache_area,
            session=session,
            llm_provider=llm_provider,
            llm_config=llm_config,
            metadata=RunMetadata(source="skill_trigger_eval", task_id=task_id),
        )
        try_attach_local_bohrium_runtime_from_env(session)

        exp = Exp(exp_config)
        cancel_ctrl = CancellationController()
        detector = _SkillTriggerDetector(cancel_ctrl, target_skill=target_skill)

        t0 = time.monotonic()
        try:
            from matmaster.core.stream_drain import drain_run_stream

            drain: DrainResult = await drain_run_stream(
                exp.run_stream(pg_ctx, prompt, cancel_token=cancel_ctrl.token),
                on_event=detector.on_event,
            )
            result.turns_used = drain.num_turns
        except Exception as e:
            err_name = type(e).__name__
            if "Cancel" in err_name:
                pass
            else:
                result.error = f"{err_name}: {e}"
                logger.warning("Case %s error: %s", task_id, result.error)
        finally:
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            try:
                session.close()
            except Exception:
                pass

        result.triggered_skills = detector.triggered_skills
        result.non_skill_tools = detector.non_skill_tools
        result.turns_used = max(result.turns_used, detector.turns_seen)

    except Exception as e:
        result.error = f"setup: {type(e).__name__}: {e}"
        logger.error("Case %s setup failed: %s", task_id, e, exc_info=True)

    return result


# ---------------------------------------------------------------------------
# Main evaluation driver
# ---------------------------------------------------------------------------


def load_cases(cases_path: Path) -> tuple[int, list[dict[str, Any]]]:
    """Load cases.yaml, return (max_turns, list of skill case dicts)."""
    with open(cases_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    max_turns = int(data.get("max_turns", _DEFAULT_MAX_TURNS))
    cases = data.get("cases", [])
    return max_turns, cases


async def run_skill_trigger_eval(
    *,
    cases_path: Path | None = None,
    mat_config_path: Path | None = None,
    output_dir: Path | None = None,
    exp_name: str = _DEFAULT_EXP_NAME,
    skills_filter: list[str] | None = None,
    max_cases: int | None = None,
    model_route: str | None = None,
    repeats: int = _DEFAULT_REPEATS,
    jobs: int = 1,
) -> list[SkillReport]:
    """Run the full skill trigger evaluation.

    Args:
        cases_path: Path to cases.yaml. Defaults to adjacent file.
        mat_config_path: Path to LLM config. Defaults to config/llm_config.yaml.
        output_dir: Where to write results. Defaults to runs/skill_trigger_eval/.
        exp_name: Exp config to use (default "direct").
        skills_filter: If set, only evaluate these skills.
        max_cases: If set, limit positive+negative cases per skill (for quick testing).
        model_route: LLM route key (e.g. "bedrock-claude-opus"). Uses llm_config default if None.
        repeats: Number of times to repeat each case (all must pass for verdict=pass).
        jobs: Number of parallel tasks (cases × repeats are parallelized).
    """
    project_root = Path(__file__).resolve().parents[2]

    if cases_path is None:
        cases_path = Path(__file__).parent / "cases.yaml"
    if mat_config_path is None:
        mat_config_path = project_root / "config" / "llm_config.yaml"
    if output_dir is None:
        output_dir = project_root / "runs" / "skill_trigger_eval"

    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = output_dir / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)

    max_turns, all_cases = load_cases(cases_path)
    reports: list[SkillReport] = []

    semaphore = asyncio.Semaphore(jobs)

    async def _run_repeat_with_semaphore(
        **kwargs: Any,
    ) -> RepeatResult:
        async with semaphore:
            return await _run_single_repeat(**kwargs)

    for skill_cases in all_cases:
        skill_name = skill_cases["skill"]
        if skills_filter and skill_name not in skills_filter:
            continue

        report = SkillReport(skill=skill_name)
        positives = skill_cases.get("positive", [])
        negatives = skill_cases.get("negative", [])

        if max_cases is not None:
            positives = positives[:max_cases]
            negatives = negatives[:max_cases]

        logger.info(
            "Evaluating skill: %s (%d pos, %d neg, k=%d, jobs=%d)",
            skill_name,
            len(positives),
            len(negatives),
            repeats,
            jobs,
        )

        # Build all tasks for this skill (positive + negative, all repeats)
        all_tasks: list[dict[str, Any]] = []
        for idx, prompt in enumerate(positives):
            for r in range(repeats):
                all_tasks.append(
                    {
                        "prompt": prompt,
                        "target_skill": skill_name,
                        "case_type": "positive",
                        "max_turns": max_turns,
                        "exp_name": exp_name,
                        "mat_config_path": mat_config_path,
                        "workspace_root": workspace_root,
                        "case_index": idx,
                        "repeat_index": r,
                        "model_route": model_route,
                    }
                )
        for idx, prompt in enumerate(negatives):
            for r in range(repeats):
                all_tasks.append(
                    {
                        "prompt": prompt,
                        "target_skill": skill_name,
                        "case_type": "negative",
                        "max_turns": max_turns,
                        "exp_name": exp_name,
                        "mat_config_path": mat_config_path,
                        "workspace_root": workspace_root,
                        "case_index": idx,
                        "repeat_index": r,
                        "model_route": model_route,
                    }
                )

        # Run all repeats in parallel (bounded by semaphore)
        coros = [_run_repeat_with_semaphore(**task) for task in all_tasks]
        all_results = await asyncio.gather(*coros)

        # Reassemble results into CaseResult objects
        result_iter = iter(all_results)
        for idx, prompt in enumerate(positives):
            case = CaseResult(skill=skill_name, prompt=prompt, case_type="positive")
            for _ in range(repeats):
                case.repeats.append(next(result_iter))
            case.verdict = "pass" if case.all_triggered_target else "fail"
            report.positive_results.append(case)
            logger.info(
                "  [+] %s case %d/%d → %s (rate=%.0f%%, %dms)",
                skill_name,
                idx + 1,
                len(positives),
                case.verdict,
                case.trigger_rate * 100,
                case.total_duration_ms,
            )

        for idx, prompt in enumerate(negatives):
            case = CaseResult(skill=skill_name, prompt=prompt, case_type="negative")
            for _ in range(repeats):
                case.repeats.append(next(result_iter))
            case.verdict = "pass" if case.none_triggered_target else "fail"
            report.negative_results.append(case)
            logger.info(
                "  [-] %s case %d/%d → %s (rate=%.0f%%, %dms)",
                skill_name,
                idx + 1,
                len(negatives),
                case.verdict,
                case.trigger_rate * 100,
                case.total_duration_ms,
            )

        reports.append(report)

    # Write results
    _write_report(reports, output_dir)
    return reports


def _write_report(reports: list[SkillReport], output_dir: Path) -> None:
    """Write JSON report and human-readable summary."""
    results_payload: list[dict[str, Any]] = []
    for report in reports:
        entry: dict[str, Any] = {
            "skill": report.skill,
            "recall": report.recall,
            "specificity": report.specificity,
            "mean_positive_trigger_rate": report.mean_positive_trigger_rate,
            "positive_pass": f"{report.positive_pass_count}/{len(report.positive_results)}",
            "negative_pass": f"{report.negative_pass_count}/{len(report.negative_results)}",
            "positive": [
                {
                    "prompt": r.prompt,
                    "verdict": r.verdict,
                    "trigger_rate": r.trigger_rate,
                    "total_duration_ms": r.total_duration_ms,
                    "repeats": [
                        {
                            "triggered_skills": rep.triggered_skills,
                            "duration_ms": rep.duration_ms,
                            "turns_used": rep.turns_used,
                            "error": rep.error,
                        }
                        for rep in r.repeats
                    ],
                }
                for r in report.positive_results
            ],
            "negative": [
                {
                    "prompt": r.prompt,
                    "verdict": r.verdict,
                    "trigger_rate": r.trigger_rate,
                    "total_duration_ms": r.total_duration_ms,
                    "repeats": [
                        {
                            "triggered_skills": rep.triggered_skills,
                            "duration_ms": rep.duration_ms,
                            "turns_used": rep.turns_used,
                            "error": rep.error,
                        }
                        for rep in r.repeats
                    ],
                }
                for r in report.negative_results
            ],
        }
        results_payload.append(entry)

    report_path = output_dir / "trigger_report.json"
    report_path.write_text(
        json.dumps(results_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Human-readable summary
    lines: list[str] = ["# Skill Trigger Evaluation Report", ""]
    lines.append(
        f"{'Skill':<25} {'Recall':<10} {'Specificity':<12} {'Pos':<8} {'Neg':<8}"
    )
    lines.append("-" * 65)
    total_pos_pass = 0
    total_pos = 0
    total_neg_pass = 0
    total_neg = 0
    for report in reports:
        lines.append(
            f"{report.skill:<25} "
            f"{report.recall:.1%}{'':>4} "
            f"{report.specificity:.1%}{'':>5} "
            f"{report.positive_pass_count}/{len(report.positive_results):<5} "
            f"{report.negative_pass_count}/{len(report.negative_results):<5}"
        )
        total_pos_pass += report.positive_pass_count
        total_pos += len(report.positive_results)
        total_neg_pass += report.negative_pass_count
        total_neg += len(report.negative_results)

    lines.append("-" * 65)
    agg_recall = total_pos_pass / total_pos if total_pos else 0
    agg_spec = total_neg_pass / total_neg if total_neg else 0
    lines.append(
        f"{'AGGREGATE':<25} "
        f"{agg_recall:.1%}{'':>4} "
        f"{agg_spec:.1%}{'':>5} "
        f"{total_pos_pass}/{total_pos:<5} "
        f"{total_neg_pass}/{total_neg:<5}"
    )
    lines.append("")

    summary_path = output_dir / "trigger_summary.txt"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report written to %s", output_dir)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Skill trigger-rate evaluation")
    parser.add_argument("--cases", type=Path, default=None, help="Path to cases.yaml")
    parser.add_argument("--config", type=Path, default=None, help="Path to LLM config")
    parser.add_argument("--output", type=Path, default=None, help="Output directory")
    parser.add_argument(
        "--exp", type=str, default=_DEFAULT_EXP_NAME, help="Exp config name"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="bedrock-claude-opus",
        help=(
            "LLM route key (e.g. 'bedrock-claude-opus', 'claude-sonnet-4-6'). "
            "See config/llm_config.yaml routes. Default: bedrock-claude-opus."
        ),
    )
    parser.add_argument(
        "--skills", nargs="*", default=None, help="Filter: only evaluate these skills"
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Max cases per type per skill (for quick test)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=_DEFAULT_REPEATS,
        help=f"Repeats per case — all must pass (default: {_DEFAULT_REPEATS})",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel tasks (default: 1, sequential)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_skill_trigger_eval(
            cases_path=args.cases,
            mat_config_path=args.config,
            output_dir=args.output,
            exp_name=args.exp,
            skills_filter=args.skills,
            max_cases=args.max_cases,
            model_route=args.model,
            repeats=args.k,
            jobs=args.jobs,
        )
    )


if __name__ == "__main__":
    main()
