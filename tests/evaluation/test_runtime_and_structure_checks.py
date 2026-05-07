"""Tests for evaluation duration_ms plumbing and structure formula checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evaluator_helpers import check_duration_budget
from evaluation.core.evidence import EvidenceBundle, TokenUsage
from evaluation.validators.answer_text import (
    check_answer_json_numeric,
    extract_json_block,
    navigate_json_path,
)
from evaluation.validators.structure_molcrys import (
    check_disorder_dan2_integer_formula,
    check_sc005_other_formulas_in_answer,
    run_sc005_formula_checks,
)


def test_duration_budget_passes_when_under_ceiling() -> None:
    ev = EvidenceBundle(
        task_id='t1',
        duration_ms=1000,
        token_usage_last_turn=TokenUsage(total_tokens=10),
    )
    ok, reason = check_duration_budget(evidence=ev, expected={'max': 5000})
    assert ok is True
    assert '1000' in reason


def test_duration_budget_fails_when_missing_duration() -> None:
    ev = EvidenceBundle(task_id='t1', duration_ms=0)
    ok, reason = check_duration_budget(evidence=ev, expected={'max': 5000})
    assert ok is False
    assert 'not recorded' in reason


# ---------------------------------------------------------------------------
# answer_json_numeric: deterministic numeric check on a JSON block in the answer
# ---------------------------------------------------------------------------


_ANSWER_TAG_SAMPLE = """
We performed Pawley refinement at each temperature. Summary follows.

<eval_results>
{
  "rtp": {
    "303K": {"a": 10.83, "V": 999.81},
    "363K": {"a": 10.92, "V": 1011.67},
    "fits": {"V": {"slope": 0.2013, "r_squared": 0.99}}
  },
  "htp": {
    "383K": {"a": 11.05, "V": 1022.73},
    "fits": {"V": {"slope": 0.1154}}
  }
}
</eval_results>

That's the result.
"""

_ANSWER_FENCE_SAMPLE = """
Refinement done.

```json
{"rtp": {"303K": {"V": 999.81}}}
```
"""


def test_answer_json_numeric_extracts_from_eval_results_tag() -> None:
    obj, reason = extract_json_block(_ANSWER_TAG_SAMPLE)
    assert obj is not None, reason
    assert obj['rtp']['303K']['V'] == 999.81


def test_answer_json_numeric_extracts_from_json_fence() -> None:
    obj, _ = extract_json_block(_ANSWER_FENCE_SAMPLE)
    assert obj is not None
    assert obj['rtp']['303K']['V'] == 999.81


def test_answer_json_numeric_no_block_fails_clearly() -> None:
    obj, reason = extract_json_block('No structured block here, just prose.')
    assert obj is None
    assert 'eval_results' in reason or 'json' in reason


def test_answer_json_numeric_invalid_json_block_reports_decode_error() -> None:
    bad = '<eval_results>{not: valid}</eval_results>'
    obj, reason = extract_json_block(bad)
    assert obj is None
    assert 'JSON' in reason or 'json' in reason


def test_navigate_json_path_handles_numeric_keys() -> None:
    obj = {'rtp': {'303K': {'V': 999.81}}}
    val, err = navigate_json_path(obj, 'rtp.303K.V')
    assert err == ''
    assert val == 999.81


def test_navigate_json_path_missing_key_lists_available() -> None:
    obj = {'rtp': {'303K': {'V': 999.81}}}
    val, err = navigate_json_path(obj, 'rtp.323K.V')
    assert val is None
    assert "'323K'" in err
    assert '303K' in err  # available keys mentioned


def test_check_answer_json_numeric_pass_within_tolerance() -> None:
    ok, reason = check_answer_json_numeric(
        _ANSWER_TAG_SAMPLE,
        json_path='rtp.fits.V.slope',
        target=0.2013,
        tolerance=0.06,
    )
    assert ok, reason
    assert "path='rtp.fits.V.slope'" in reason


def test_check_answer_json_numeric_fail_outside_tolerance() -> None:
    ok, reason = check_answer_json_numeric(
        _ANSWER_TAG_SAMPLE,
        json_path='htp.fits.V.slope',
        target=0.5,
        tolerance=0.05,
    )
    assert not ok
    assert 'found=0.1154' in reason


def test_check_answer_json_numeric_does_not_fall_back_to_other_keys() -> None:
    """Regression for the numerical_range pitfall: a lookup must not silently
    pick a number from a different field (different temperature/phase) just
    because the magnitude happens to be closest."""
    ok, reason = check_answer_json_numeric(
        _ANSWER_TAG_SAMPLE,
        json_path='rtp.303K.V',
        target=1022.73,  # this is HTP/383K — must NOT pass on RTP/303K key
        tolerance=10.0,
    )
    assert not ok
    assert 'found=999.81' in reason


def test_check_answer_json_numeric_rejects_non_numeric_value() -> None:
    answer = '<eval_results>{"rtp":{"303K":{"V":"approx 999.8"}}}</eval_results>'
    ok, reason = check_answer_json_numeric(
        answer, json_path='rtp.303K.V', target=999.81, tolerance=20.0
    )
    assert not ok
    assert 'not numeric' in reason


def test_check_answer_json_numeric_via_evaluator_helper_dispatch() -> None:
    """End-to-end: ReferenceAnswer.value as a dict drives the verifier."""
    from evaluation.core.evaluator_helpers import (
        check_answer_json_numeric_from_ref,
    )
    from evaluation.core.schemas import ReferenceAnswer

    ref = ReferenceAnswer(
        key='rtp_V_303K',
        value={'json_path': 'rtp.303K.V', 'target': 999.81, 'tolerance': 20.0},
    )
    ok, reason = check_answer_json_numeric_from_ref(answer=_ANSWER_TAG_SAMPLE, ref=ref)
    assert ok, reason


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


def test_sc005_formulas_reordered_elements() -> None:
    """Agent reports same formulas but with different element ordering."""
    answer = """
    disorder_DAN-2.cif
    chemical_formula: K1H14C6N5O9
    disorder_DAP-4: C48H144Cl24N24O96
    disorder_PAP-H4: C80H288Cl48N48O192
    disorder_PAP-M5: C40H112Ag8Cl24N16O96
    disorder_TILPEN: C24Fe2H40N16O2
    """
    ok, reason = run_sc005_formula_checks(answer)
    assert ok is True, reason


def test_sc005_formulas_reduced_form() -> None:
    """Agent reports reduced-ratio formulas instead of full-cell counts."""
    answer = """
    disorder_DAN-2.cif
    chemical_formula: K1H14C6N5O9
    disorder_DAP-4: C2H6NClO4
    disorder_PAP-H4: C5H18Cl3N3O12
    disorder_PAP-M5: Ag1C5H14Cl3N2O12
    disorder_TILPEN: FeH20C12N8O
    """
    ok, reason = run_sc005_formula_checks(answer)
    assert ok is True, reason


def test_sc005_formulas_implicit_count_1() -> None:
    """Token extraction handles formulas with implicit count-1 elements (e.g. FeC5N6O)."""
    from evaluation.validators.structure_molcrys import _extract_formula_like_tokens

    tokens = _extract_formula_like_tokens('The formula is FeC5N6O and also C2H6NClO4')
    assert 'FeC5N6O' in tokens
    assert 'C2H6NClO4' in tokens


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
        empty_completion_max_retries=0,
    )
    assert 'duration_ms' in out
    assert isinstance(out['duration_ms'], int)
    assert out['duration_ms'] >= 0


def test_run_mat_task_empty_completion_retry_sums_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second attempt runs when first is completed/natural with no answer or tools."""
    from evaluation.core import mat_runner

    first = {
        "task_id": "tid",
        "mode": "direct",
        "answer": "",
        "tool_calls": [],
        "result": {"status": "completed", "reason": "natural"},
        "status": "completed",
        "duration_ms": 10,
        "trajectory_path": "",
    }
    second = {
        **first,
        "answer": "recovered",
        "duration_ms": 20,
    }
    seq = iter([first, second])

    def _fake_once(**kwargs: object) -> dict:
        return next(seq)

    monkeypatch.setattr(mat_runner, "_run_mat_task_once", _fake_once)
    out = mat_runner.run_mat_task(
        prompt="hi",
        mode="direct",
        task_id="tid",
        run_dir=tmp_path,
        mat_config_path=Path("configs/mat_master/config.yaml"),
        empty_completion_max_retries=1,
    )
    assert out["answer"] == "recovered"
    assert out["empty_completion_retry_count"] == 1
    assert out["duration_ms"] == 30


def test_eval_run_record_serializes_duration_ms() -> None:
    from evaluation.core.schemas import EvalRunRecord

    r = EvalRunRecord(
        question_id='Q',
        capability='structure_construction',
        domain='battery',
        mode='direct',
        repeat_idx=0,
        prompt='p',
        answer='a',
        run_status='completed',
        duration_ms=1234,
    )
    dumped = json.loads(r.model_dump_json())
    assert dumped['duration_ms'] == 1234


def test_question_item_rejects_removed_capability_knowledge_recall() -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match='knowledge_recall'):
        QuestionItem(
            id='KR',
            capability='knowledge_recall',
            domain='battery',
            intent='legacy capability should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )


def test_question_item_rejects_removed_domain_optical() -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match='optical'):
        QuestionItem(
            id='OP',
            capability='structure_construction',
            domain='optical',
            intent='legacy domain should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )


@pytest.mark.parametrize(
    'domain',
    [
        'battery',
        'catalysis',
        'polymer',
        'alloy',
        'semiconductor',
    ],
)
def test_question_item_accepts_business_line_domains(domain: str) -> None:
    from evaluation.core.schemas import QuestionItem

    item = QuestionItem(
        id=f'{domain}_ok',
        capability='scientific_analysis',
        domain=domain,
        intent='new business-line domain should be accepted',
        human_prompt_seed='x',
        scoring_checklist=[
            {
                'id': 'unused',
                'criterion': 'unused',
                'axis': 'correctness',
                'verify': 'llm_binary_judge',
            }
        ],
        reference_answers=[{'key': 'unused', 'value': 'x'}],
    )
    assert item.domain == domain


@pytest.mark.parametrize(
    'domain',
    [
        'struct',
        'elec',
        'mech',
        'thermo',
        'kinetic',
        'general',
        'incar',
        'scxrd',
        'mlip',
    ],
)
def test_question_item_rejects_removed_legacy_domains(domain: str) -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match=domain):
        QuestionItem(
            id='legacy_domain',
            capability='scientific_analysis',
            domain=domain,
            intent='legacy domain should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )


@pytest.mark.parametrize(
    'capability',
    [
        'property_prediction',
        'input_generation_vasp',
        'input_generation_abacus',
        'co2rr_reproduction',
    ],
)
def test_question_item_rejects_removed_legacy_capabilities(capability: str) -> None:
    from evaluation.core.schemas import QuestionItem

    with pytest.raises(ValidationError, match=capability):
        QuestionItem(
            id='legacy_cap',
            capability=capability,
            domain='battery',
            intent='legacy capability should be rejected',
            human_prompt_seed='x',
            scoring_checklist=[
                {
                    'id': 'unused',
                    'criterion': 'unused',
                    'axis': 'correctness',
                    'verify': 'llm_binary_judge',
                }
            ],
            reference_answers=[{'key': 'unused', 'value': 'x'}],
        )


def test_safety_questions_also_count_token_and_duration_efficiency() -> None:
    from evaluation.core.schemas import QuestionItem, SafetyVetoRecord, TokenUsageRecord

    evaluator = BinaryEvaluator()
    q = QuestionItem(
        id='SR',
        capability='safety_refusal',
        domain='battery',
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
    importlib.util.find_spec('pymatgen') is None,
    reason='pymatgen optional; install with uv sync --extra calculation',
)
def test_check_layer_count_counts_nine_atomic_planes(tmp_path: Path) -> None:
    """Nine z-planes spaced 0.5 Å apart should count as 9 layers (layer_tol=0.25)."""
    from pymatgen.core import Lattice, Structure

    lattice = Lattice.orthorhombic(2.0, 2.0, 10.0)
    species: list[str] = []
    frac_coords: list[list[float]] = []
    for i in range(9):
        z_cart = 0.5 + float(i) * 0.5
        z_frac = z_cart / 10.0
        species.extend(['Fe', 'Fe'])
        frac_coords.append([0.25, 0.25, z_frac])
        frac_coords.append([0.75, 0.75, z_frac + 0.002])
    struct = Structure(lattice, species, frac_coords)
    struct.to(filename=str(tmp_path / 'nine_planes.cif'), fmt='cif')

    from evaluation.validators.structure_general import check_layer_count

    ok, msg = check_layer_count(
        tmp_path,
        filename='nine_planes.cif',
        expected=9,
        tolerance=0,
        axis='z',
        layer_tol_A=0.25,
    )
    assert ok is True, msg


@pytest.mark.skipif(
    importlib.util.find_spec('pymatgen') is None,
    reason='pymatgen optional; install with uv sync --extra calculation',
)
def test_check_layer_count_three_coarse_blocks_old_gap_method_would_be_three(
    tmp_path: Path,
) -> None:
    """Three trilayer blocks (9 atomic planes): gap-based counting collapses to 3."""
    from pymatgen.core import Lattice, Structure

    lattice = Lattice.orthorhombic(2.0, 2.0, 30.0)
    species: list[str] = []
    frac_coords: list[list[float]] = []
    for base_cart in (1.0, 10.0, 19.0):
        for d in (0.0, 0.5, 1.0):
            z_cart = base_cart + d
            z_frac = z_cart / 30.0
            species.append('Ce')
            frac_coords.append([0.5, 0.5, z_frac])
            species.append('O')
            frac_coords.append([0.0, 0.0, z_frac + 0.03 / 30.0])
    struct = Structure(lattice, species, frac_coords)
    struct.to(filename=str(tmp_path / 'trilayers.cif'), fmt='cif')

    from evaluation.validators.structure_general import check_layer_count

    ok9, msg9 = check_layer_count(
        tmp_path,
        filename='trilayers.cif',
        expected=9,
        tolerance=0,
        axis='z',
        layer_tol_A=0.25,
    )
    assert ok9 is True, msg9

    coords = sorted(float(s.coords[2]) for s in struct.sites)
    import numpy as _np

    diffs = _np.diff(coords)
    n_gap_layers = 1 + int(_np.sum(diffs > 0.8))
    assert n_gap_layers == 3


@pytest.mark.skipif(
    importlib.util.find_spec('pymatgen') is None,
    reason='pymatgen optional; install with uv sync --extra calculation',
)
def test_struct_file_parsable_accepts_valid_cif(tmp_path: Path) -> None:
    from pymatgen.core import Lattice, Structure

    struct = Structure(
        Lattice.cubic(3.0),
        ['Li'],
        [[0.0, 0.0, 0.0]],
    )
    struct.to(filename=str(tmp_path / 'valid.cif'), fmt='cif')

    from evaluation.validators.structure_general import check_parsable

    ok, reason = check_parsable(tmp_path, filename='valid.cif')
    assert ok is True, reason


def test_struct_file_parsable_rejects_invalid_file(tmp_path: Path) -> None:
    (tmp_path / 'broken.cif').write_text('not a valid structure file')

    from evaluation.validators.structure_general import check_parsable

    ok, reason = check_parsable(tmp_path, filename='broken.cif')
    if importlib.util.find_spec('pymatgen') is None:
        assert ok is False
        assert 'pymatgen not installed' in reason
    else:
        assert ok is False
        assert 'could not parse' in reason


@pytest.mark.skipif(
    importlib.util.find_spec('pymatgen') is None,
    reason='pymatgen optional; install with uv sync --extra calculation',
)
def test_all_occupancy_one_accepts_ordered_cif(tmp_path: Path) -> None:
    from pymatgen.core import Lattice, Structure

    struct = Structure(
        Lattice.cubic(3.0),
        ['Li', 'O'],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )
    struct.to(filename=str(tmp_path / 'ordered_a.cif'), fmt='cif')
    struct.to(filename=str(tmp_path / 'ordered_b.cif'), fmt='cif')

    from evaluation.validators.structure_general import check_all_occupancy_one

    ok, reason = check_all_occupancy_one(tmp_path, filename='ordered_*.cif')
    assert ok is True, reason


@pytest.mark.skipif(
    importlib.util.find_spec('pymatgen') is None,
    reason='pymatgen optional; install with uv sync --extra calculation',
)
def test_all_occupancy_one_rejects_split_occupancy(tmp_path: Path) -> None:
    from pymatgen.core import Lattice, Structure

    struct = Structure(
        Lattice.cubic(3.0),
        [{'Li': 0.5, 'Na': 0.5}],
        [[0.0, 0.0, 0.0]],
    )
    struct.to(filename=str(tmp_path / 'ordered_bad.cif'), fmt='cif')

    from evaluation.validators.structure_general import check_all_occupancy_one

    ok, reason = check_all_occupancy_one(tmp_path, filename='ordered_*.cif')
    assert ok is False
    assert 'split species' in reason or 'occupancy' in reason


def test_removed_slab_centered_verify_is_rejected() -> None:
    from evaluation.core.schemas import ScoringCheckItem

    with pytest.raises(ValidationError):
        ScoringCheckItem(
            id='legacy',
            criterion='legacy slab-centering verifier should stay removed',
            verify='struct_file_slab_centered',
        )


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


@pytest.mark.skipif(
    importlib.util.find_spec('molcrys_kit') is None,
    reason='molcrys-kit optional; install with uv sync --extra calculation',
)
def test_molcrys_local_env_graceful_missing_workspace() -> None:
    """check_molcrys_local_env fails gracefully on non-existent workspace."""
    from evaluation.validators.structure_molcrys import check_molcrys_local_env

    ok, reason = check_molcrys_local_env(
        '/nonexistent/path',
        filename='dacmor_hydrogenated.cif',
        expected_formula='C21H23NO5',
        z_value=4,
    )
    assert ok is False
    assert 'not a directory' in reason


def test_molcrys_local_env_returns_not_installed_when_missing() -> None:
    """Without molcrys-kit, the function returns a clear error, not an exception."""
    from evaluation.validators.structure_molcrys import check_molcrys_local_env

    # If MolCrysKit IS installed, this test still validates return type
    ok, reason = check_molcrys_local_env(
        '/nonexistent/path',
        filename='test.cif',
        expected_formula='C21H23NO5',
        z_value=4,
    )
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
