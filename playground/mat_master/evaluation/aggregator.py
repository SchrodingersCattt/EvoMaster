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

    # v4: three-dimensional score accumulators
    by_level_accuracy: dict[str, list[float]] = defaultdict(list)
    by_level_grounding: dict[str, list[float]] = defaultdict(list)
    by_level_efficiency: dict[str, list[float]] = defaultdict(list)
    by_level_strict_final: dict[str, list[float]] = defaultdict(list)
    by_level_analysis_final: dict[str, list[float]] = defaultdict(list)

    by_mode_accuracy: dict[str, list[float]] = defaultdict(list)
    by_mode_grounding: dict[str, list[float]] = defaultdict(list)
    by_mode_efficiency: dict[str, list[float]] = defaultdict(list)
    by_mode_strict_final: dict[str, list[float]] = defaultdict(list)
    by_mode_analysis_final: dict[str, list[float]] = defaultdict(list)

    # v4: model-level accumulators
    by_model_scores: dict[str, list[float]] = defaultdict(list)
    by_model_strict_final: dict[str, list[float]] = defaultdict(list)
    by_model_analysis_final: dict[str, list[float]] = defaultdict(list)
    by_model_tokens: dict[str, list[int]] = defaultdict(list)

    grouped: dict[tuple[str, str], list[EvalRunRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.question_id, record.mode)].append(record)
        by_level_scores[record.level].append(record.band_score)
        by_mode_scores[record.mode].append(record.band_score)
        if record.safety_veto.triggered:
            safety_triggered += 1

        # Accumulate three-dimensional scores
        level, mode = record.level, record.mode
        _acc_optional(by_level_accuracy[level], record.accuracy_score)
        _acc_optional(by_level_grounding[level], record.grounding_score)
        _acc_optional(by_level_efficiency[level], record.efficiency_score)
        _acc_optional(by_level_strict_final[level], record.strict_final)
        _acc_optional(by_level_analysis_final[level], record.analysis_final)

        _acc_optional(by_mode_accuracy[mode], record.accuracy_score)
        _acc_optional(by_mode_grounding[mode], record.grounding_score)
        _acc_optional(by_mode_efficiency[mode], record.efficiency_score)
        _acc_optional(by_mode_strict_final[mode], record.strict_final)
        _acc_optional(by_mode_analysis_final[mode], record.analysis_final)

        # Model-level accumulation
        model_key = record.model_name or 'unknown'
        by_model_scores[model_key].append(record.band_score)
        _acc_optional(by_model_strict_final[model_key], record.strict_final)
        _acc_optional(by_model_analysis_final[model_key], record.analysis_final)
        if record.token_usage and record.token_usage.total_tokens > 0:
            by_model_tokens[model_key].append(record.token_usage.total_tokens)

    # Build per-question summary
    for (question_id, mode), items in grouped.items():
        scores = [item.band_score for item in items]
        key = f"{question_id}:{mode}"
        acc_scores = [i.accuracy_score for i in items if i.accuracy_score is not None]
        grd_scores = [i.grounding_score for i in items if i.grounding_score is not None]
        eff_scores = [i.efficiency_score for i in items if i.efficiency_score is not None]
        strict_scores = [i.strict_final for i in items if i.strict_final is not None]
        analysis_scores = [i.analysis_final for i in items if i.analysis_final is not None]
        by_question[key] = {
            'question_id': question_id,
            'mode': mode,
            'n': len(scores),
            'mean': _safe_mean(scores),
            'std': _safe_std(scores),
            'min': min(scores) if scores else 0.0,
            'max': max(scores) if scores else 0.0,
            'safety_veto_count': sum(1 for item in items if item.safety_veto.triggered),
            # v4 dimensional means
            'accuracy_mean': _safe_mean(acc_scores) if acc_scores else None,
            'grounding_mean': _safe_mean(grd_scores) if grd_scores else None,
            'efficiency_mean': _safe_mean(eff_scores) if eff_scores else None,
            'strict_final_mean': _safe_mean(strict_scores) if strict_scores else None,
            'analysis_final_mean': _safe_mean(analysis_scores) if analysis_scores else None,
        }

    # Build by-level summary with v4 dimensions
    by_level: dict[str, Any] = {}
    for level, scores in by_level_scores.items():
        stats = _score_stats(scores)
        stats.update(_dim_stats_block(
            accuracy=list(by_level_accuracy[level]),
            grounding=list(by_level_grounding[level]),
            efficiency=list(by_level_efficiency[level]),
            strict_final=list(by_level_strict_final[level]),
            analysis_final=list(by_level_analysis_final[level]),
        ))
        by_level[level] = stats

    # Build by-mode summary with v4 dimensions
    by_mode: dict[str, Any] = {}
    for mode, scores in by_mode_scores.items():
        stats = _score_stats(scores)
        stats.update(_dim_stats_block(
            accuracy=list(by_mode_accuracy[mode]),
            grounding=list(by_mode_grounding[mode]),
            efficiency=list(by_mode_efficiency[mode]),
            strict_final=list(by_mode_strict_final[mode]),
            analysis_final=list(by_mode_analysis_final[mode]),
        ))
        by_mode[mode] = stats

    # Build overall
    all_scores = [record.band_score for record in records]
    overall = _score_stats(all_scores)
    all_accuracy = [r.accuracy_score for r in records if r.accuracy_score is not None]
    all_grounding = [r.grounding_score for r in records if r.grounding_score is not None]
    all_efficiency = [r.efficiency_score for r in records if r.efficiency_score is not None]
    all_strict = [r.strict_final for r in records if r.strict_final is not None]
    all_analysis = [r.analysis_final for r in records if r.analysis_final is not None]
    overall.update(_dim_stats_block(
        accuracy=all_accuracy,
        grounding=all_grounding,
        efficiency=all_efficiency,
        strict_final=all_strict,
        analysis_final=all_analysis,
    ))
    overall['safety_veto_rate'] = (safety_triggered / len(records)) if records else 0.0
    overall['passed'] = safety_triggered == 0

    # Build by-model summary
    by_model: dict[str, Any] = {}
    for model_key in sorted(set(by_model_scores.keys())):
        model_stats = _score_stats(list(by_model_scores[model_key]))
        strict_vals = list(by_model_strict_final[model_key])
        analysis_vals = list(by_model_analysis_final[model_key])
        token_vals = list(by_model_tokens[model_key])
        model_stats['strict_final_mean'] = _safe_mean(strict_vals) if strict_vals else None
        model_stats['analysis_final_mean'] = _safe_mean(analysis_vals) if analysis_vals else None
        model_stats['total_tokens_sum'] = sum(token_vals) if token_vals else 0
        model_stats['total_tokens_mean'] = _safe_mean(token_vals) if token_vals else None
        model_stats['runs_with_token_data'] = len(token_vals)
        by_model[model_key] = model_stats

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
        by_model=by_model,
    )


def _acc_optional(lst: list[float], value: float | None) -> None:
    """Append value to list only if not None."""
    if value is not None:
        lst.append(value)


def _dim_stats_block(
    *,
    accuracy: list[float],
    grounding: list[float],
    efficiency: list[float],
    strict_final: list[float],
    analysis_final: list[float],
) -> dict[str, Any]:
    """Return v4 dimensional stats dict for embedding into a stats block."""
    return {
        'accuracy_mean': _safe_mean(accuracy) if accuracy else None,
        'accuracy_std': _safe_std(accuracy) if len(accuracy) > 1 else None,
        'grounding_mean': _safe_mean(grounding) if grounding else None,
        'grounding_std': _safe_std(grounding) if len(grounding) > 1 else None,
        'efficiency_mean': _safe_mean(efficiency) if efficiency else None,
        'efficiency_std': _safe_std(efficiency) if len(efficiency) > 1 else None,
        'strict_final_mean': _safe_mean(strict_final) if strict_final else None,
        'analysis_final_mean': _safe_mean(analysis_final) if analysis_final else None,
    }


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
