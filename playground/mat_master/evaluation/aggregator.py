"""Aggregation utilities for MATTER evaluation outputs."""

import math
from collections import defaultdict
from statistics import mean, stdev
from typing import Any

from .schemas import EvalRunRecord, EvaluationSummary


def build_summary(records: list[EvalRunRecord]) -> EvaluationSummary:
    by_question: dict[str, dict[str, Any]] = {}
    by_level_scores: dict[str, list[float]] = defaultdict(list)
    by_mode_scores: dict[str, list[float]] = defaultdict(list)
    safety_triggered = 0

    grouped: dict[tuple[str, str], list[EvalRunRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.question_id, record.mode)].append(record)
        by_level_scores[record.level].append(record.band_score)
        by_mode_scores[record.mode].append(record.band_score)
        if record.safety_veto.triggered:
            safety_triggered += 1

    for (question_id, mode), items in grouped.items():
        scores = [item.band_score for item in items]
        key = f"{question_id}:{mode}"
        by_question[key] = {
            'question_id': question_id,
            'mode': mode,
            'n': len(scores),
            'mean': _safe_mean(scores),
            'std': _safe_std(scores),
            'min': min(scores) if scores else 0.0,
            'max': max(scores) if scores else 0.0,
            'safety_veto_count': sum(1 for item in items if item.safety_veto.triggered),
        }

    by_level: dict[str, Any] = {}
    for level, scores in by_level_scores.items():
        by_level[level] = _score_stats(scores)

    by_mode: dict[str, Any] = {}
    for mode, scores in by_mode_scores.items():
        by_mode[mode] = _score_stats(scores)

    all_scores = [record.band_score for record in records]
    overall = _score_stats(all_scores)
    overall['safety_veto_rate'] = (safety_triggered / len(records)) if records else 0.0
    overall['passed'] = safety_triggered == 0

    safety = {
        'triggered_count': safety_triggered,
        'total_runs': len(records),
        'triggered_rate': (safety_triggered / len(records)) if records else 0.0,
        'any_triggered': safety_triggered > 0,
    }

    return EvaluationSummary(
        total_runs=len(records),
        by_question=by_question,
        by_level=by_level,
        by_mode=by_mode,
        overall=overall,
        safety=safety,
    )


def _t_critical(df: int) -> float:
    """Two-tailed 95% t critical value for small samples, z=1.96 fallback for df>=30."""
    table = {
        1: 12.706,
        2: 4.303,
        3: 3.182,
        4: 2.776,
        5: 2.571,
        6: 2.447,
        7: 2.365,
        8: 2.306,
        9: 2.262,
        10: 2.228,
        15: 2.131,
        20: 2.086,
        25: 2.060,
        29: 2.045,
    }
    if df >= 30:
        return 1.96
    best_df = max(k for k in table if k <= df) if any(k <= df for k in table) else 1
    return table[best_df]


def _score_stats(scores: list[float]) -> dict[str, Any]:
    n = len(scores)
    if n == 0:
        return {'n': 0, 'mean': 0.0, 'std': 0.0, 'ci95_half_width': 0.0}
    avg = _safe_mean(scores)
    std = _safe_std(scores)
    t_crit = _t_critical(n - 1) if n > 1 else 0.0
    ci = t_crit * std / math.sqrt(n) if n > 1 else 0.0
    return {
        'n': n,
        'mean': avg,
        'std': std,
        'ci95_half_width': ci,
        'min': min(scores),
        'max': max(scores),
    }


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(mean(values))


def _safe_std(values: list[float]) -> float:
    if not values or len(values) == 1:
        return 0.0
    return float(stdev(values))
