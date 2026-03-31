"""Tests for evaluation duration_ms plumbing and structure formula checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evaluator_helpers import check_duration_budget
from evaluation.core.evidence import EvidenceBundle, TokenUsage
from evaluation.validators.structure_molcrys import (
    check_disorder_dan2_integer_formula,
    check_sc005_other_formulas_in_answer,
    run_sc005_formula_checks,
)


def test_duration_budget_passes_when_under_ceiling() -> None:
    ev = EvidenceBundle(
        task_id='t1',
        duration_ms=1000,
        token_usage=TokenUsage(total_tokens=10),
    )
    ok, reason = check_duration_budget(evidence=ev, expected={'max': 5000})
    assert ok is True
    assert '1000' in reason


def test_duration_budget_fails_when_missing_duration() -> None:
    ev = EvidenceBundle(task_id='t1', duration_ms=0)
    ok, reason = check_duration_budget(evidence=ev, expected={'max': 5000})
    assert ok is False
    assert 'not recorded' in reason


def test_sc005_other_formulas_detects_missing() -> None:
    ok, reason = check_sc005_other_formulas_in_answer('disorder_DAP-4 nonsense')
    assert ok is False
    assert 'missing' in reason.lower()


def test_sc005_dan2_rejects_fractional() -> None:
    bad = """
    disorder_DAN-2.cif
    chemical_formula: K1H13.9872C5N5O9
    """
    ok, reason = check_disorder_dan2_integer_formula(bad)
    assert ok is False
    assert 'fractional' in reason.lower() or '13.98' in reason


def test_sc005_dan2_accepts_integer() -> None:
    good = """
    disorder_DAN-2.cif
    chemical_formula: K1H14C6N5O9
    """ + '\n'.join(
        f'x {s}'
        for s in [
            'H144C48N24Cl24O96',
            'H288C80N48Cl48O192',
            'Ag8H112C40N16Cl24O96',
            'Fe2H40C24N16O2',
        ]
    )
    ok, reason = run_sc005_formula_checks(good)
    assert ok is True, reason


def test_mat_runner_includes_duration_ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: run_mat_task records monotonic wall time (playground mocked)."""
    from evaluation.core import mat_runner

    class _FakePlayground:
        log_file_handler = None
        _log_file_stream = None

        def set_run_dir(self, *args, **kwargs) -> None:
            return None

        def set_mode(self, *args, **kwargs) -> None:
            return None

        def run(self, task_description: str = '') -> dict:
            return {'status': 'completed', 'trajectory': None}

    monkeypatch.setattr(
        mat_runner,
        'get_playground_class',
        lambda name, config_path=None: _FakePlayground(),
    )
    out = mat_runner.run_mat_task(
        prompt='hi',
        mode='direct',
        task_id='tid',
        run_dir=tmp_path,
        mat_config_path=Path('configs/mat_master/config.yaml'),
    )
    assert 'duration_ms' in out
    assert isinstance(out['duration_ms'], int)
    assert out['duration_ms'] >= 0


def test_eval_run_record_serializes_duration_ms() -> None:
    from evaluation.core.schemas import EvalRunRecord

    r = EvalRunRecord(
        question_id='Q',
        capability='structure_construction',
        domain='struct',
        mode='direct',
        repeat_idx=0,
        prompt='p',
        answer='a',
        run_status='completed',
        duration_ms=1234,
    )
    dumped = json.loads(r.model_dump_json())
    assert dumped['duration_ms'] == 1234


def test_safety_questions_also_count_token_and_duration_efficiency() -> None:
    from evaluation.core.schemas import QuestionItem, SafetyVetoRecord, TokenUsageRecord

    evaluator = BinaryEvaluator()
    q = QuestionItem(
        id='SR',
        capability='safety_refusal',
        domain='general',
        intent='refuse harmful request',
        human_prompt_seed='x',
        scoring_checklist=[
            {
                'id': 'unused',
                'criterion': 'unused',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
        reference_answers=[],
    )
    evaluator.evaluate_safety = lambda **kwargs: SafetyVetoRecord(  # type: ignore[method-assign]
        triggered=False,
        reason='',
        risk_not_detected=True,
        detail_non_leakage=True,
        safe_redirection=True,
    )
    rec = evaluator.evaluate(
        question=q,
        answer='I cannot help with that.',
        token_usage=TokenUsageRecord(
            prompt_tokens=10, completion_tokens=5, total_tokens=20
        ),
        duration_ms=1000,
    )
    assert rec.correctness_total == 1
    assert rec.efficiency_total == 2
    assert rec.criteria_results['token_budget_total'].passed is True
    assert rec.criteria_results['duration_budget'].passed is True


@pytest.mark.skipif(
    importlib.util.find_spec('molcrys_kit') is None,
    reason='molcrys-kit optional; install with uv sync --extra calculation',
)
def test_molcrys_slab_scaling_placeholder() -> None:
    """If MolCrysKit is installed, empty workspace should fail gracefully."""
    from evaluation.validators.structure_molcrys import (
        verify_molecular_slab_layer_scaling,
    )

    ok, reason = verify_molecular_slab_layer_scaling('/nonexistent/path')
    assert ok is False
