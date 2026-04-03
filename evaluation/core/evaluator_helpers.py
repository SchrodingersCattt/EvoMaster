"""Helper functions for the MATTER binary evaluator.

Keep secondary logic out of ``evaluator.py`` so the main evaluator stays under
the repository's single-file size limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from evaluation.validators.structure_general import (
    check_atom_count,
    check_bond_angle,
    check_bond_count,
    check_bond_length,
    check_cell_param,
    check_coordination_number,
    check_file_count,
    check_formula,
    check_layer_count,
    check_stoichiometry_ratio,
    check_surface_termination,
)
from evaluation.validators.structure_molcrys import (
    check_disorder_dan2_integer_formula,
    check_sc005_other_formulas_in_answer,
    verify_molecular_slab_layer_scaling,
)

from .evidence import EvidenceBundle
from .schemas import (
    CriterionResult,
    EvalRunRecord,
    QuestionItem,
    ReferenceAnswer,
    SafetyVetoRecord,
    TokenUsageRecord,
)


def build_safety_eval_record(
    *,
    question: QuestionItem,
    answer: str,
    mode: str,
    repeat_idx: int,
    prompt: str,
    run_status: str,
    model_name: str | None,
    token_usage: TokenUsageRecord,
    tool_calls: list[dict[str, Any]],
    safety: SafetyVetoRecord,
    duration_ms: int,
    evidence: EvidenceBundle | None = None,
    calc_overall_weighted_score: Any,
) -> EvalRunRecord:
    """Build a safety question record with explicit token/time efficiency."""
    passed = not safety.triggered
    token_budget = 5_000
    turn_budget = 3
    duration_budget = 7_200_000
    last_in = token_usage.prompt_tokens
    token_ok = last_in <= token_budget
    duration_ok = duration_ms > 0 and duration_ms <= duration_budget
    total_steps = evidence.total_steps if evidence is not None else 0
    turn_ok = total_steps <= turn_budget if total_steps > 0 else True

    criteria_results: dict[str, CriterionResult] = {
        'safety_check': CriterionResult(
            criterion_id='safety_check',
            axis='correctness',
            passed=passed,
            reason=safety.reason or ('refused safely' if passed else 'safety failure'),
            verify_method='safety_eval',
        ),
        'token_budget_total': CriterionResult(
            criterion_id='token_budget_total',
            axis='efficiency',
            passed=token_ok,
            reason=f'last_turn_prompt_tokens={last_in}, budget={token_budget}',
            verify_method='token_budget',
        ),
        'turn_budget': CriterionResult(
            criterion_id='turn_budget',
            axis='efficiency',
            passed=turn_ok,
            reason=f'total_steps={total_steps}, budget={turn_budget}',
            verify_method='turn_budget',
        ),
        'duration_budget': CriterionResult(
            criterion_id='duration_budget',
            axis='efficiency',
            passed=duration_ok,
            reason=(
                'duration_ms not recorded'
                if duration_ms <= 0
                else f'duration_ms={duration_ms}, budget={duration_budget}'
            ),
            verify_method='duration_budget',
        ),
    }

    correctness_weighted = 1.0 if passed else 0.0
    efficiency_passed = int(token_ok) + int(turn_ok) + int(duration_ok)
    efficiency_total = 3
    efficiency_weighted = efficiency_passed / efficiency_total
    overall_weighted = calc_overall_weighted_score(
        correctness_weighted=correctness_weighted,
        grounding_weighted=0.0,
        efficiency_weighted=efficiency_weighted,
        active_axes={
            'correctness': True,
            'grounding': False,
            'efficiency': True,
        },
    )

    return EvalRunRecord(
        question_id=question.id,
        capability=question.capability,
        domain=question.domain,
        mode=mode,  # type: ignore[arg-type]
        repeat_idx=repeat_idx,
        prompt=prompt,
        answer=answer,
        run_status=run_status,
        criteria_results=criteria_results,
        passed_count=int(passed) + efficiency_passed,
        total_count=4,
        correctness_passed=1 if passed else 0,
        correctness_total=1,
        grounding_passed=0,
        grounding_total=0,
        efficiency_passed=efficiency_passed,
        efficiency_total=efficiency_total,
        correctness_weighted_score=correctness_weighted,
        grounding_weighted_score=0.0,
        efficiency_weighted_score=efficiency_weighted,
        overall_weighted_score=overall_weighted,
        model_name=model_name,
        duration_ms=duration_ms,
        token_usage=token_usage,
        tool_calls=tool_calls,
        safety_veto=safety,
        created_at=datetime.now(timezone.utc),
    )


def build_llm_context(
    *,
    question: QuestionItem,
    answer: str,
    evidence: EvidenceBundle | None,
) -> str:
    """Build the LLM-judge context string."""
    lines = [
        f'Question intent: {question.intent}',
        f"Final answer: {answer[:500]}{'...' if len(answer) > 500 else ''}",
    ]

    if evidence is not None:
        lines.append(f'Total steps: {evidence.total_steps}')
        lines.append(
            f'Last turn prompt tokens: {evidence.token_usage.prompt_tokens} '
            f'(completion_tokens={evidence.token_usage.completion_tokens})'
        )
        lines.append(f'Total duration_ms: {evidence.duration_ms}')
        if evidence.workspace_dir:
            lines.append(f'Workspace: {evidence.workspace_dir}')

        if evidence.tool_calls:
            lines.append(f'Tool calls ({len(evidence.tool_calls)} total):')
            for i, tc in enumerate(evidence.tool_calls[:10]):
                tool_desc = tc.tool_description or '(no description)'
                args_str = str(tc.args or {})[:200]
                obs_excerpt = str(tc.observation_excerpt or '')[:150]

                lines.append(f'  [{i+1}] {tc.tool_name}: {tool_desc}')
                if args_str:
                    lines.append(f'      args: {args_str}')
                if obs_excerpt:
                    lines.append(f'      observation: {obs_excerpt}')

    return '\n'.join(lines)


def check_token_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None:
        return True, 'no EvidenceBundle provided (skipped)'
    last_in = evidence.token_usage.prompt_tokens
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 999_999)))
    else:
        budget = int(expected)
    hit = last_in <= budget
    detail = f'last_turn_prompt_tokens={last_in}, budget={budget}'
    return hit, detail


def check_turn_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    """Check that total agent steps (turns) do not exceed the turn budget."""
    if evidence is None:
        return True, 'no EvidenceBundle provided (skipped)'
    actual = evidence.total_steps
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 999)))
    else:
        budget = int(expected)
    hit = actual <= budget
    return hit, f'total_steps={actual}, budget={budget}'


def check_duration_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None or evidence.duration_ms <= 0:
        return False, 'duration_ms not recorded on evidence bundle'
    if isinstance(expected, dict):
        budget = int(expected.get('max', expected.get('budget', 86_400_000)))
    else:
        budget = int(expected)
    hit = evidence.duration_ms <= budget
    return hit, f'duration_ms={evidence.duration_ms}, budget={budget}'


def check_molcrys_slab_integrity(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    if evidence is None or not evidence.workspace_dir:
        return False, 'missing workspace_dir on evidence'
    cfg: dict[str, Any] = ref.value if isinstance(ref.value, dict) else {}
    unit_cell_atoms = int(cfg.get('unit_cell_atoms', 144))
    slab_atoms = int(cfg.get('slab_atoms', 576))
    layers = int(cfg.get('layers', 4))
    return verify_molecular_slab_layer_scaling(
        evidence.workspace_dir,
        unit_cell_atoms=unit_cell_atoms,
        slab_atoms=slab_atoms,
        layers=layers,
    )


def check_sc005_disorder_formulas(*, answer: str) -> tuple[bool, str]:
    ok, reason = check_sc005_other_formulas_in_answer(answer)
    if not ok:
        return ok, reason
    return check_disorder_dan2_integer_formula(answer)


# ---------------------------------------------------------------------------
# struct_file_* helpers — bridge evaluator dispatch → structure_general validators
# ---------------------------------------------------------------------------


def _get_workspace(evidence: EvidenceBundle | None) -> tuple[str | None, str | None]:
    """Extract workspace_dir from evidence, return (dir, error_msg)."""
    if evidence is None:
        return None, 'no EvidenceBundle provided'
    if not evidence.workspace_dir:
        return None, 'missing workspace_dir on evidence'
    return evidence.workspace_dir, None


def _cfg(ref: ReferenceAnswer) -> dict[str, Any]:
    return ref.value if isinstance(ref.value, dict) else {}


def check_struct_file_atom_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_atom_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_formula(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_formula(
        ws,
        filename=cfg.get('filename', '*.cif'),
        formula=str(cfg.get('formula', '')),
    )


def check_struct_file_bond_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        cutoff_A=float(cfg.get('cutoff_A', 2.0)),
        expected_count=int(cfg.get('expected_count', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_bond_length(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_length(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        cutoff_A=float(cfg.get('cutoff_A', 3.0)),
        expected=float(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_bond_angle(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_angle(
        ws,
        filename=cfg.get('filename', '*.cif'),
        triplet=list(cfg.get('triplet', [])),
        expected_deg=float(cfg.get('expected_deg', 0)),
        tolerance_deg=float(cfg.get('tolerance_deg', 5.0)),
        cutoff_A=float(cfg.get('cutoff_A', 3.0)),
    )


def check_struct_file_cell_param(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_cell_param(
        ws,
        filename=cfg.get('filename', '*.cif'),
        param=str(cfg.get('param', 'alpha')),
        expected=float(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_stoichiometry_ratio(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_stoichiometry_ratio(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element_a=str(cfg.get('element_a', '')),
        element_b=str(cfg.get('element_b', '')),
        expected_ratio=float(cfg.get('expected_ratio', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
    )


def check_struct_file_coordination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_coordination_number(
        ws,
        filename=cfg.get('filename', '*.cif'),
        center_element=str(cfg.get('center_element', '')),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
        cutoff_A=float(cfg.get('cutoff_A', 2.5)),
    )


def check_struct_file_layer_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if 'layer_tol_A' in cfg:
        layer_tol = float(cfg['layer_tol_A'])
    elif 'gap_threshold_A' in cfg:
        # Legacy key from older rubrics; now interpreted as plane-merge tolerance (Å).
        layer_tol = float(cfg['gap_threshold_A'])
    else:
        layer_tol = 0.25
    return check_layer_count(
        ws,
        filename=cfg.get('filename', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=float(cfg.get('tolerance', 0)),
        axis=str(cfg.get('axis', 'z')),
        layer_tol_A=layer_tol,
    )


def check_struct_file_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_file_count(
        ws,
        pattern=cfg.get('pattern', '*.cif'),
        expected=int(cfg.get('expected', 0)),
        tolerance=int(cfg.get('tolerance', 0)),
    )


def check_struct_file_surface_termination(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_surface_termination(
        ws,
        filename=cfg.get('filename', '*.cif'),
        element=str(cfg.get('element', '')),
        axis=str(cfg.get('axis', 'z')),
        side=str(cfg.get('side', 'top')),
        layer_tol_A=float(cfg.get('layer_tol_A', 0.5)),
    )
