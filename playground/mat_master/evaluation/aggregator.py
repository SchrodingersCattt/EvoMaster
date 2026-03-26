"""Aggregation utilities for MATTER v5 evaluation outputs.

v5 changes (vs v4):
- Replaced three-dimensional float accumulation with binary pass-count aggregation
- EvaluationSummary now uses AxisPassRates (passed, total) tuples
- by_level replaced by by_capability and by_domain
- Model comparison now uses AxisPassRates instead of float means
- _score_stats / _dim_stats_block removed (float statistics no longer needed)
- Safety summary unchanged
"""

from collections import defaultdict
from typing import Any

from .schemas import AxisPassRates, EvalRunRecord, EvaluationSummary, QuestionPassRate


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_summary(records: list[EvalRunRecord]) -> EvaluationSummary:
    """Aggregate a list of EvalRunRecords into an EvaluationSummary.

    All arithmetic is integer pass-count addition — no floats, no weights.
    """
    if not records:
        return EvaluationSummary(
            total_runs=0,
            total_criteria=0,
            total_passed=0,
            pass_rate=0.0,
        )

    total_criteria = 0
    total_passed = 0
    safety_triggered = 0

    # Accumulators keyed by (capability | domain | mode | model)
    # Each value: [correctness_passed, correctness_total,
    #              grounding_passed, grounding_total,
    #              efficiency_passed, efficiency_total]
    _CapKey = str
    _DomKey = str
    _ModeKey = str
    _ModelKey = str

    cap_acc: dict[_CapKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    dom_acc: dict[_DomKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    mode_acc: dict[_ModeKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    model_acc: dict[_ModelKey, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0])

    # Per-question accumulator: keyed by (question_id, mode)
    q_acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    # 7 slots: [cp, ct, gp, gt, ep, et, safety_veto_count]
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

        if record.safety_veto.triggered:
            safety_triggered += 1

        _add6(cap_acc[record.capability], cp, ct, gp, gt, ep, et)
        _add6(dom_acc[record.domain], cp, ct, gp, gt, ep, et)
        _add6(mode_acc[record.mode], cp, ct, gp, gt, ep, et)

        model_key = record.model_name or 'unknown'
        _add6(model_acc[model_key], cp, ct, gp, gt, ep, et)

        qk = (record.question_id, record.mode)
        _add7(q_acc[qk], cp, ct, gp, gt, ep, et,
              1 if record.safety_veto.triggered else 0)
        if qk not in q_meta:
            q_meta[qk] = {
                'capability': record.capability,
                'domain': record.domain,
            }

    pass_rate = total_passed / total_criteria if total_criteria > 0 else 0.0

    by_capability = {k: _to_axis_pass_rates(v) for k, v in cap_acc.items()}
    by_domain = {k: _to_axis_pass_rates(v) for k, v in dom_acc.items()}
    by_mode = {k: _to_axis_pass_rates(v) for k, v in mode_acc.items()}
    by_model = {k: _to_axis_pass_rates(v) for k, v in model_acc.items()}

    by_question: dict[str, QuestionPassRate] = {}
    for (question_id, mode), slots in q_acc.items():
        cp, ct, gp, gt, ep, et, sv = slots
        meta = q_meta.get((question_id, mode), {})
        overall_p = cp + gp + ep
        overall_t = ct + gt + et
        key = f"{question_id}:{mode}"
        by_question[key] = QuestionPassRate(
            question_id=question_id,
            capability=meta.get('capability', ''),
            domain=meta.get('domain', ''),
            runs=sum(
                1 for r in records
                if r.question_id == question_id and r.mode == mode
            ),
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
    cp: int, ct: int,
    gp: int, gt: int,
    ep: int, et: int,
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
    cp: int, ct: int,
    gp: int, gt: int,
    ep: int, et: int,
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


def _to_axis_pass_rates(slots: list[int]) -> AxisPassRates:
    """Convert a 6-slot [cp,ct,gp,gt,ep,et] list to AxisPassRates."""
    cp, ct, gp, gt, ep, et = slots[:6]
    overall_p = cp + gp + ep
    overall_t = ct + gt + et
    return AxisPassRates(
        correctness=(cp, ct),
        grounding=(gp, gt),
        efficiency=(ep, et),
        overall=(overall_p, overall_t),
    )
