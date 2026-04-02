"""Aggregation utilities for MATTER v5 evaluation outputs.

This module aggregates run records into both raw pass-count and weighted score summaries,
grouped by capability, domain, mode, and model.

Scoring model:
- Raw counts: sum of pass/total per axis (for backward compatibility and debugging)
- Weighted scores: calculated by averaging per-record weighted scores per axis/group
"""

from collections import defaultdict
from typing import Any

from .schemas import AxisPassRates, EvalRunRecord, EvaluationSummary, QuestionPassRate

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_summary(records: list[EvalRunRecord]) -> EvaluationSummary:
    """Aggregate a list of EvalRunRecords into an EvaluationSummary.

    Produces both raw pass-count statistics and weighted scores.
    """
    if not records:
        return EvaluationSummary(
            total_runs=0,
            total_criteria=0,
            total_passed=0,
            pass_rate=0.0,
            weighted_pass_rate=0.0,
        )

    total_criteria = 0
    total_passed = 0
    total_weighted_score = 0.0
    safety_triggered = 0

    # Accumulators keyed by (capability | domain | mode | model)
    # Raw counts: [correctness_passed, correctness_total,
    #             grounding_passed, grounding_total,
    #             efficiency_passed, efficiency_total]
    # Weighted: [correctness_weighted_sum, grounding_weighted_sum, efficiency_weighted_sum]
    _CapKey = str
    _DomKey = str
    _ModeKey = str
    _ModelKey = str

    cap_acc: dict[_CapKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    cap_weighted: dict[_CapKey, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    dom_acc: dict[_DomKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    dom_weighted: dict[_DomKey, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    mode_acc: dict[_ModeKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    mode_weighted: dict[_ModeKey, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    model_acc: dict[_ModelKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    model_weighted: dict[_ModelKey, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    # Per-question accumulator: keyed by (question_id, mode)
    q_acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    # 7 slots: [cp, ct, gp, gt, ep, et, safety_veto_count]
    q_weighted: dict[tuple[str, str], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0]
    )
    q_meta: dict[tuple[str, str], dict[str, Any]] = {}

    for record in records:
        cp = record.correctness_passed
        ct = record.correctness_total
        gp = record.grounding_passed
        gt = record.grounding_total
        ep = record.efficiency_passed
        et = record.efficiency_total

        total_criteria += record.total_count
        total_passed += record.passed_count
        total_weighted_score += record.overall_weighted_score

        if record.safety_veto.triggered:
            safety_triggered += 1

        _add6(cap_acc[record.capability], cp, ct, gp, gt, ep, et)
        cap_weighted[record.capability][0] += record.correctness_weighted_score
        cap_weighted[record.capability][1] += record.grounding_weighted_score
        cap_weighted[record.capability][2] += record.efficiency_weighted_score

        _add6(dom_acc[record.domain], cp, ct, gp, gt, ep, et)
        dom_weighted[record.domain][0] += record.correctness_weighted_score
        dom_weighted[record.domain][1] += record.grounding_weighted_score
        dom_weighted[record.domain][2] += record.efficiency_weighted_score

        _add6(mode_acc[record.mode], cp, ct, gp, gt, ep, et)
        mode_weighted[record.mode][0] += record.correctness_weighted_score
        mode_weighted[record.mode][1] += record.grounding_weighted_score
        mode_weighted[record.mode][2] += record.efficiency_weighted_score

        model_key = record.model_name or 'unknown'
        _add6(model_acc[model_key], cp, ct, gp, gt, ep, et)
        model_weighted[model_key][0] += record.correctness_weighted_score
        model_weighted[model_key][1] += record.grounding_weighted_score
        model_weighted[model_key][2] += record.efficiency_weighted_score

        qk = (record.question_id, record.mode)
        _add7(
            q_acc[qk], cp, ct, gp, gt, ep, et, 1 if record.safety_veto.triggered else 0
        )
        q_weighted[qk][0] += record.correctness_weighted_score
        q_weighted[qk][1] += record.grounding_weighted_score
        q_weighted[qk][2] += record.efficiency_weighted_score

        if qk not in q_meta:
            q_meta[qk] = {
                'capability': record.capability,
                'domain': record.domain,
            }

    pass_rate = total_passed / total_criteria if total_criteria > 0 else 0.0
    weighted_pass_rate = total_weighted_score / len(records) if records else 0.0

    by_capability = {
        k: _to_axis_pass_rates(v, cap_weighted.get(k, [0.0, 0.0, 0.0]), len(records))
        for k, v in cap_acc.items()
    }
    by_domain = {
        k: _to_axis_pass_rates(v, dom_weighted.get(k, [0.0, 0.0, 0.0]), len(records))
        for k, v in dom_acc.items()
    }
    by_mode = {
        k: _to_axis_pass_rates(v, mode_weighted.get(k, [0.0, 0.0, 0.0]), len(records))
        for k, v in mode_acc.items()
    }
    by_model = {
        k: _to_axis_pass_rates(v, model_weighted.get(k, [0.0, 0.0, 0.0]), len(records))
        for k, v in model_acc.items()
    }

    by_question: dict[str, QuestionPassRate] = {}
    for (question_id, mode), slots in q_acc.items():
        cp, ct, gp, gt, ep, et, sv = slots
        meta = q_meta.get((question_id, mode), {})
        overall_p = cp + gp + ep
        overall_t = ct + gt + et

        # Count how many records match this question+mode combo
        q_record_count = sum(
            1 for r in records if r.question_id == question_id and r.mode == mode
        )

        key = f'{question_id}:{mode}'
        by_question[key] = QuestionPassRate(
            question_id=question_id,
            capability=meta.get('capability', ''),
            domain=meta.get('domain', ''),
            runs=q_record_count,
            overall=(overall_p, overall_t),
            correctness=(cp, ct),
            grounding=(gp, gt),
            efficiency=(ep, et),
            safety_veto_count=sv,
        )

    safety = {
        'triggered_count': safety_triggered,
        'total_runs': len(records),
        'triggered_rate': safety_triggered / len(records) if records else 0.0,
        'any_triggered': safety_triggered > 0,
    }

    return EvaluationSummary(
        total_runs=len(records),
        total_criteria=total_criteria,
        total_passed=total_passed,
        pass_rate=pass_rate,
        weighted_pass_rate=weighted_pass_rate,
        by_capability=by_capability,
        by_domain=by_domain,
        by_question=by_question,
        by_mode=by_mode,
        by_model=by_model,
        safety=safety,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add6(
    slots: list[int],
    cp: int,
    ct: int,
    gp: int,
    gt: int,
    ep: int,
    et: int,
) -> None:
    """Add six pass/total integers into a 6-slot accumulator in place."""
    slots[0] += cp
    slots[1] += ct
    slots[2] += gp
    slots[3] += gt
    slots[4] += ep
    slots[5] += et


def _add7(
    slots: list[int],
    cp: int,
    ct: int,
    gp: int,
    gt: int,
    ep: int,
    et: int,
    sv: int,
) -> None:
    """Add seven values into a 7-slot accumulator in place."""
    slots[0] += cp
    slots[1] += ct
    slots[2] += gp
    slots[3] += gt
    slots[4] += ep
    slots[5] += et
    slots[6] += sv


def _to_axis_pass_rates(
    raw_slots: list[int],
    weighted_sums: list[float],
    total_records: int,
) -> AxisPassRates:
    """Convert raw counts and weighted sums to AxisPassRates."""
    cp, ct, gp, gt, ep, et = raw_slots[:6]
    overall_p = cp + gp + ep
    overall_t = ct + gt + et

    # Calculate average weighted scores
    weighted_sums[0] / total_records if total_records > 0 else 0.0
    weighted_sums[1] / total_records if total_records > 0 else 0.0
    weighted_sums[2] / total_records if total_records > 0 else 0.0

    # Store both raw pass-rate and weighted score in AxisPassRates
    # Raw tuples will still be used for backward compatibility
    return AxisPassRates(
        correctness=(cp, ct),
        grounding=(gp, gt),
        efficiency=(ep, et),
        overall=(overall_p, overall_t),
    )
