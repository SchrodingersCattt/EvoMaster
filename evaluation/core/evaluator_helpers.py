"""Helper functions for the MATTER binary evaluator.

Keep secondary logic out of ``evaluator.py`` so the main evaluator stays under
the repository's single-file size limit.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.validators.abacus_input import check_abacus_input
from evaluation.validators.answer_text import check_answer_json_numeric
from evaluation.validators.stru_file import check_stru_file
from evaluation.validators.structure_general import (
    check_atom_count,
    check_bond_angle,
    check_bond_count,
    check_bond_length,
    check_bond_length_range,
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
    check_molcrys_local_env,
    check_sc005_other_formulas_in_answer,
    verify_molecular_slab_layer_scaling,
)
from evaluation.validators.text_file import (
    check_text_file_contains_all,
    check_text_file_kpt_path,
    check_text_file_numeric_range,
    check_text_file_regex,
)

from .evidence import EvidenceBundle, TokenUsage
from .schemas import (
    CriterionResult,
    EvalRunRecord,
    QuestionItem,
    ReferenceAnswer,
    SafetyVetoRecord,
    TokenUsageRecord,
)


def _last_turn_raw_total_tokens_for_budget(rec: TokenUsageRecord) -> int:
    """Last-round reported ``total_tokens`` for budgets (no cache subtraction)."""
    if rec.total_tokens > 0:
        return rec.total_tokens
    tu = TokenUsage.from_usage_dict(
        {
            "prompt_tokens": rec.prompt_tokens,
            "completion_tokens": rec.completion_tokens,
            "total_tokens": rec.total_tokens,
            "cache_read_tokens": rec.cache_read_tokens,
        }
    )
    if tu.total_tokens > 0:
        return tu.total_tokens
    return max(0, tu.prompt_tokens + tu.completion_tokens)


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
    measured_tokens = _last_turn_raw_total_tokens_for_budget(token_usage)
    token_ok = measured_tokens <= token_budget
    duration_ok = duration_ms > 0 and duration_ms <= duration_budget
    total_steps = evidence.total_steps if evidence is not None else 0
    has_turn_evidence = total_steps > 0
    turn_ok = total_steps <= turn_budget if has_turn_evidence else False

    criteria_results: dict[str, CriterionResult] = {
        "safety_check": CriterionResult(
            criterion_id="safety_check",
            axis="correctness",
            passed=passed,
            reason=safety.reason or ("refused safely" if passed else "safety failure"),
            verify_method="safety_eval",
        ),
        "token_budget_total": CriterionResult(
            criterion_id="token_budget_total",
            axis="efficiency",
            passed=token_ok,
            reason=(f"last_turn_total_tokens={measured_tokens}, budget={token_budget}"),
            verify_method="token_budget",
        ),
        "duration_budget": CriterionResult(
            criterion_id="duration_budget",
            axis="efficiency",
            passed=duration_ok,
            reason=(
                "duration_ms not recorded"
                if duration_ms <= 0
                else f"duration_ms={duration_ms}, budget={duration_budget}"
            ),
            verify_method="duration_budget",
        ),
    }
    if has_turn_evidence:
        criteria_results["turn_budget"] = CriterionResult(
            criterion_id="turn_budget",
            axis="efficiency",
            passed=turn_ok,
            reason=f"total_steps={total_steps}, budget={turn_budget}",
            verify_method="turn_budget",
        )

    correctness_weighted = 1.0 if passed else 0.0
    efficiency_passed = int(token_ok) + int(duration_ok)
    efficiency_total = 2
    total_count = 3
    if has_turn_evidence:
        efficiency_passed += int(turn_ok)
        efficiency_total += 1
        total_count += 1
    efficiency_weighted = efficiency_passed / efficiency_total
    overall_weighted = calc_overall_weighted_score(
        correctness_weighted=correctness_weighted,
        grounding_weighted=0.0,
        efficiency_weighted=efficiency_weighted,
        active_axes={
            "correctness": True,
            "grounding": False,
            "efficiency": True,
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
        total_count=total_count,
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
    ref: ReferenceAnswer | None = None,
    include_tool_calls: bool = True,
) -> str:
    """Build the LLM-judge context string.

    When ``include_tool_calls`` is False (e.g. grounding-axis judges), tool-call
    lines are omitted so the judge does not treat missing MCP/web_search as
    evidence of failure.
    Workspace output filenames are still listed when artifacts are present.
    """
    lines = [
        f"Question intent: {question.intent}",
        f"Final answer: {answer[:4000]}{'...' if len(answer) > 4000 else ''}",
    ]

    if evidence is not None:
        lines.append(f"Total steps: {evidence.total_steps}")
        lines.append(
            f"Last turn prompt tokens: {evidence.token_usage_last_turn.prompt_tokens} "
            f"(completion_tokens={evidence.token_usage_last_turn.completion_tokens})"
        )
        lines.append(f"Total duration_ms: {evidence.duration_ms}")
        if evidence.workspace_dir:
            lines.append(f"Workspace: {evidence.workspace_dir}")

        if evidence.artifacts and not include_tool_calls:
            names = [a.path for a in evidence.artifacts[:40]]
            lines.append(
                f'Workspace output files (names only, up to 40): {", ".join(names)}'
            )
            if len(evidence.artifacts) > 40:
                lines.append(
                    f"  … and {len(evidence.artifacts) - 40} more files not listed."
                )

        if include_tool_calls and evidence.tool_calls:
            lines.append(f"Tool calls ({len(evidence.tool_calls)} total):")
            for i, tc in enumerate(evidence.tool_calls[:10]):
                tool_desc = tc.tool_description or "(no description)"
                args_str = str(tc.args or {})[:200]
                obs_excerpt = str(tc.observation_excerpt or "")[:150]

                lines.append(f"  [{i+1}] {tc.tool_name}: {tool_desc}")
                if args_str:
                    lines.append(f"      args: {args_str}")
                if obs_excerpt:
                    lines.append(f"      observation: {obs_excerpt}")

        # For llm_binary_judge criteria with referenced file artifacts,
        # inject file content excerpt so judge decisions are based on output content.
        if ref is not None and evidence.workspace_dir:
            cfg = ref.value if isinstance(ref.value, dict) else {}
            filenames_raw = []
            if cfg:
                one = str(cfg.get("filename", "")).strip()
                if one:
                    filenames_raw.append(one)
                many = cfg.get("filenames")
                if isinstance(many, list):
                    filenames_raw.extend(str(x).strip() for x in many if str(x).strip())
            if filenames_raw:
                seen: set[str] = set()
                filenames = []
                for name in filenames_raw:
                    if name not in seen:
                        seen.add(name)
                        filenames.append(name)
                workspace_resolve = ref.workspace_resolve or "recursive"
                root = Path(evidence.workspace_dir)
                max_chars = 6000

                def _resolve_target(filename: str) -> Path | None:
                    if workspace_resolve == "root":
                        if len(Path(filename).parts) == 1:
                            cand = root / filename
                            if cand.is_file():
                                return cand
                        return None
                    exact = root / filename
                    if exact.is_file():
                        return exact
                    hits = [
                        p
                        for p in root.rglob("*")
                        if p.is_file() and fnmatch.fnmatch(p.name, filename)
                    ]
                    if not hits:
                        return None
                    return max(hits, key=lambda p: p.stat().st_mtime)

                for filename in filenames:
                    resolved = _resolve_target(filename)
                    if resolved is None:
                        lines.append(
                            f"Referenced file for criterion not found: {filename}"
                        )
                        continue
                    try:
                        raw = resolved.read_text(encoding="utf-8")
                        excerpt = raw[:max_chars]
                        lines.append(
                            f"Referenced file for criterion: {filename} "
                            f"(resolved: {resolved.name})"
                        )
                        if raw:
                            lines.append("Referenced file content excerpt:")
                            lines.append(excerpt)
                            if len(raw) > max_chars:
                                lines.append(f"... [truncated, total chars={len(raw)}]")
                        else:
                            lines.append("Referenced file is empty.")
                    except Exception as exc:
                        lines.append(
                            f"Failed to read referenced file {filename}: {exc}"
                        )

    return "\n".join(lines)


def check_token_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None:
        return True, "no EvidenceBundle provided (skipped)"
    lt = evidence.token_usage_last_turn
    measured = lt.total_tokens
    if measured <= 0:
        tu = TokenUsage(
            prompt_tokens=lt.prompt_tokens,
            completion_tokens=lt.completion_tokens,
            total_tokens=lt.total_tokens,
            cache_read_tokens=lt.cache_read_tokens,
        )
        measured = (
            tu.total_tokens
            if tu.total_tokens > 0
            else max(0, tu.prompt_tokens + tu.completion_tokens)
        )
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999_999)))
    else:
        budget = int(expected)
    hit = measured <= budget
    detail = f"last_turn_total_tokens={measured}, budget={budget}"
    return hit, detail


def check_turn_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    """Check that total agent steps (turns) do not exceed the turn budget."""
    if evidence is None:
        return True, "no EvidenceBundle provided (skipped)"
    actual = evidence.total_steps
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 999)))
    else:
        budget = int(expected)
    hit = actual <= budget
    return hit, f"total_steps={actual}, budget={budget}"


def token_usage_record_from_evidence(evidence: EvidenceBundle) -> TokenUsageRecord:
    """Snapshot **last LLM turn** (raw ``total_tokens``, no cache deduction in
    budgets).
    """
    src = evidence.token_usage_last_turn
    raw_total = src.total_tokens
    return TokenUsageRecord(
        prompt_tokens=src.prompt_tokens,
        completion_tokens=src.completion_tokens,
        total_tokens=raw_total,
        cache_read_tokens=src.cache_read_tokens,
        total_tokens_effective=raw_total,
    )


def check_duration_budget(
    *, evidence: EvidenceBundle | None, expected: Any
) -> tuple[bool, str]:
    if evidence is None or evidence.duration_ms <= 0:
        return False, "duration_ms not recorded on evidence bundle"
    if isinstance(expected, dict):
        budget = int(expected.get("max", expected.get("budget", 86_400_000)))
    else:
        budget = int(expected)
    hit = evidence.duration_ms <= budget
    return hit, f"duration_ms={evidence.duration_ms}, budget={budget}"


def check_molcrys_slab_integrity(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    if evidence is None or not evidence.workspace_dir:
        return False, "missing workspace_dir on evidence"
    cfg: dict[str, Any] = ref.value if isinstance(ref.value, dict) else {}
    unit_cell_atoms = int(cfg.get("unit_cell_atoms", 144))
    slab_atoms = int(cfg.get("slab_atoms", 576))
    layers = int(cfg.get("layers", 4))
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


def check_molcrys_local_env_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Bridge evaluator dispatch → MolCrysKit local-environment validator."""
    if evidence is None or not evidence.workspace_dir:
        return False, "missing workspace_dir on evidence"
    cfg: dict[str, Any] = ref.value if isinstance(ref.value, dict) else {}
    filename = cfg.get("filename", "*.cif")
    expected_formula = cfg.get("expected_formula", "")
    z_value = int(cfg.get("z_value", 4))
    if not expected_formula:
        return False, "reference answer missing expected_formula"
    return check_molcrys_local_env(
        evidence.workspace_dir,
        filename=filename,
        expected_formula=expected_formula,
        z_value=z_value,
    )


# ---------------------------------------------------------------------------
# struct_file_* helpers — bridge evaluator dispatch → structure_general validators
# ---------------------------------------------------------------------------


def _get_workspace(evidence: EvidenceBundle | None) -> tuple[str | None, str | None]:
    """Extract workspace_dir from evidence, return (dir, error_msg)."""
    if evidence is None:
        return None, "no EvidenceBundle provided"
    if not evidence.workspace_dir:
        return None, "missing workspace_dir on evidence"
    return evidence.workspace_dir, None


def _cfg(ref: ReferenceAnswer) -> dict[str, Any]:
    return ref.value if isinstance(ref.value, dict) else {}


def _workspace_resolve_from_ref(ref: ReferenceAnswer) -> str:
    """Plain-text / artifact checks: recursive (legacy) vs workspace root only."""
    return ref.workspace_resolve or "recursive"


def check_struct_file_atom_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_atom_count(
        ws,
        filename=cfg.get("filename", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        element=str(cfg.get("element")) if cfg.get("element") else None,
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
        filename=cfg.get("filename", "*.cif"),
        formula=str(cfg.get("formula", "")),
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
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 2.0)),
        expected_count=int(cfg.get("expected_count", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
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
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        expected=float(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
    )


def check_struct_file_bond_length_range(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    return check_bond_length_range(
        ws,
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        expected_min=float(cfg.get("expected_min", 0.0)),
        expected_max=float(cfg.get("expected_max", 0.0)),
    )


def check_struct_file_bond_angle(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)

    def _opt(name: str) -> float | None:
        val = cfg.get(name)
        return None if val is None else float(val)

    return check_bond_angle(
        ws,
        filename=cfg.get("filename", "*.cif"),
        triplet=list(cfg.get("triplet", [])),
        expected_deg=float(cfg.get("expected_deg", 0)),
        tolerance_deg=float(cfg.get("tolerance_deg", 5.0)),
        cutoff_A=float(cfg.get("cutoff_A", 3.0)),
        cutoff_a_b_A=_opt("cutoff_a_b_A"),
        cutoff_c_b_A=_opt("cutoff_c_b_A"),
        cutoff_a_b_min_A=float(cfg.get("cutoff_a_b_min_A", 0.0)),
        cutoff_c_b_min_A=float(cfg.get("cutoff_c_b_min_A", 0.0)),
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
        filename=cfg.get("filename", "*.cif"),
        param=str(cfg.get("param", "alpha")),
        expected=float(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
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
        filename=cfg.get("filename", "*.cif"),
        element_a=str(cfg.get("element_a", "")),
        element_b=str(cfg.get("element_b", "")),
        expected_ratio=float(cfg.get("expected_ratio", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
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
        filename=cfg.get("filename", "*.cif"),
        center_element=str(cfg.get("center_element", "")),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        cutoff_A=float(cfg.get("cutoff_A", 2.5)),
    )


def check_struct_file_layer_count(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    if "layer_tol_A" in cfg:
        layer_tol = float(cfg["layer_tol_A"])
    elif "gap_threshold_A" in cfg:
        # Legacy key from older rubrics; now interpreted as plane-merge tolerance (Å).
        layer_tol = float(cfg["gap_threshold_A"])
    else:
        layer_tol = 0.25
    return check_layer_count(
        ws,
        filename=cfg.get("filename", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=float(cfg.get("tolerance", 0)),
        axis=str(cfg.get("axis", "z")),
        layer_tol_A=layer_tol,
        element=str(cfg.get("element")) if cfg.get("element") else None,
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
        pattern=cfg.get("pattern", "*.cif"),
        expected=int(cfg.get("expected", 0)),
        tolerance=int(cfg.get("tolerance", 0)),
    )


def check_checkcif_alerts(
    *,
    evidence: EvidenceBundle | None,
    ref: ReferenceAnswer,
) -> tuple[bool, str]:
    """Evaluate checkcif_no_a_alerts: find CIF in workspace, run checkCIF.

    ref.value must be a dict with optional keys:
      - filename (str, default '*.cif'): glob pattern to find the CIF
      - max_a_alerts (int, default 0): maximum allowed A-level alerts
    """
    from evaluation.validators.checkcif import check_checkcif_no_a_alerts

    workspace_dir, _ = _get_workspace(evidence)
    if workspace_dir is None:
        return False, "no workspace directory available in evidence"

    val = ref.value or {}
    filename = val.get("filename", "*.cif") if isinstance(val, dict) else "*.cif"
    max_a_alerts = int(val.get("max_a_alerts", 0)) if isinstance(val, dict) else 0

    return check_checkcif_no_a_alerts(
        workspace_dir,
        filename=filename,
        max_a_alerts=max_a_alerts,
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
        filename=cfg.get("filename", "*.cif"),
        element=str(cfg.get("element", "")),
        axis=str(cfg.get("axis", "z")),
        side=str(cfg.get("side", "top")),
        layer_tol_A=float(cfg.get("layer_tol_A", 0.5)),
    )


def check_text_file_contains_all_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_tokens = cfg.get("tokens", [])
    if not isinstance(raw_tokens, list) or not raw_tokens:
        return False, "reference answer must provide non-empty 'tokens' list"
    flags = str(cfg.get("flags", "")).lower()
    case_sensitive = bool(cfg.get("case_sensitive", False))
    if "i" in flags:
        case_sensitive = False
    return check_text_file_contains_all(
        ws,
        filename=str(cfg.get("filename", "")),
        tokens=[str(token) for token in raw_tokens],
        case_sensitive=case_sensitive,
        normalize_whitespace=bool(cfg.get("normalize_whitespace", True)),
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_text_file_regex_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    flags = str(cfg.get("flags", ""))
    resolve_mode = _workspace_resolve_from_ref(ref)

    if_pattern = str(cfg.get("if_pattern", "")).strip()
    then_pattern = str(cfg.get("then_pattern", "")).strip()
    else_pattern = str(cfg.get("else_pattern", "")).strip()
    if if_pattern or then_pattern or else_pattern:
        if not (if_pattern and then_pattern and else_pattern):
            return (
                False,
                "conditional regex requires non-empty 'if_pattern', 'then_pattern', and 'else_pattern'",
            )
        if_filename = str(cfg.get("if_filename", cfg.get("filename", ""))).strip()
        if not if_filename:
            return False, "conditional regex requires 'if_filename' or 'filename'"
        else_filename = str(cfg.get("else_filename", "")).strip() or if_filename

        cond_ok, cond_reason = check_text_file_regex(
            ws,
            filename=if_filename,
            pattern=if_pattern,
            flags=flags,
            workspace_resolve=resolve_mode,
        )
        if cond_ok:
            then_ok, then_reason = check_text_file_regex(
                ws,
                filename=if_filename,
                pattern=then_pattern,
                flags=flags,
                workspace_resolve=resolve_mode,
            )
            return (
                then_ok,
                f"conditional regex IF matched on {if_filename}: {cond_reason}; THEN result: {then_reason}",
            )

        else_ok, else_reason = check_text_file_regex(
            ws,
            filename=else_filename,
            pattern=else_pattern,
            flags=flags,
            workspace_resolve=resolve_mode,
        )
        return (
            else_ok,
            f"conditional regex IF not matched on {if_filename}: {cond_reason}; ELSE result on {else_filename}: {else_reason}",
        )

    raw_filenames = cfg.get("filenames")
    if isinstance(raw_filenames, list) and raw_filenames:
        filenames = [str(name).strip() for name in raw_filenames if str(name).strip()]
        if not filenames:
            return False, "reference answer must provide non-empty 'filenames' list"
        raw_patterns = cfg.get("patterns")
        if raw_patterns is None:
            shared_pattern = str(cfg.get("pattern", "")).strip()
            if not shared_pattern:
                return (
                    False,
                    "multi-file regex requires 'pattern' or non-empty 'patterns' list",
                )
            patterns = [shared_pattern] * len(filenames)
        else:
            if not isinstance(raw_patterns, list) or not raw_patterns:
                return (
                    False,
                    "reference answer 'patterns' must be a non-empty list when provided",
                )
            patterns = [str(p).strip() for p in raw_patterns]
            if len(patterns) != len(filenames):
                return (
                    False,
                    "'patterns' length must equal 'filenames' length for multi-file regex",
                )
            if any(not p for p in patterns):
                return False, "all entries in 'patterns' must be non-empty"
        min_match_count = int(cfg.get("min_match_count", len(filenames)))
        if min_match_count < 1:
            return False, "'min_match_count' must be >= 1"
        if min_match_count > len(filenames):
            return (
                False,
                f"'min_match_count'={min_match_count} exceeds number of files {len(filenames)}",
            )
        matched = 0
        details: list[str] = []
        for filename, pattern in zip(filenames, patterns, strict=False):
            ok, reason = check_text_file_regex(
                ws,
                filename=filename,
                pattern=pattern,
                flags=flags,
                workspace_resolve=resolve_mode,
            )
            details.append(f"{filename}: {reason}")
            if ok:
                matched += 1
        passed = matched >= min_match_count
        return (
            passed,
            (
                f"multi-file regex matched {matched}/{len(filenames)} files "
                f"(required >= {min_match_count}); " + "; ".join(details)
            ),
        )

    pattern = str(cfg.get("pattern", ""))
    if not pattern:
        return False, "reference answer must provide non-empty 'pattern'"
    return check_text_file_regex(
        ws,
        filename=str(cfg.get("filename", "")),
        pattern=pattern,
        flags=flags,
        workspace_resolve=resolve_mode,
    )


def check_text_file_numeric_range_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_checks = cfg.get("checks", [])
    if not isinstance(raw_checks, list) or not raw_checks:
        return False, "reference answer must provide non-empty 'checks' list"
    checks: list[dict[str, Any]] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            return False, "each entry in 'checks' must be a dict"
        checks.append(item)
    return check_text_file_numeric_range(
        ws,
        filename=str(cfg.get("filename", "")),
        checks=checks,
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_text_file_kpt_path_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    raw_required = cfg.get("required_points", [])
    if not isinstance(raw_required, list) or not raw_required:
        return False, "reference answer must provide non-empty 'required_points' list"
    required_points: list[list[float]] = []
    for item in raw_required:
        if not isinstance(item, list) or len(item) != 3:
            return False, "each entry in 'required_points' must be [x, y, z]"
        try:
            required_points.append([float(item[0]), float(item[1]), float(item[2])])
        except (TypeError, ValueError):
            return False, "required_points entries must be numeric"
    return check_text_file_kpt_path(
        ws,
        filename=str(cfg.get("filename", "")),
        required_points=required_points,
        tolerance=float(cfg.get("tolerance", 1.0e-6)),
        require_line_mode=bool(cfg.get("require_line_mode", True)),
        require_order=bool(cfg.get("require_order", True)),
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_answer_json_numeric_from_ref(
    *, answer: str, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Wire ``answer_json_numeric`` from a ``ReferenceAnswer`` config.

    Reference answer schema (``ref.value`` is a dict)::

        value:
          json_path: rtp.303K.V   # dot-separated dict keys in the answer JSON
          target: 999.81          # numeric target (or use ref-level ``value``+``tolerance``)
          tolerance: 20.0         # absolute tolerance

    For backward compatibility, when ``ref.value`` is plain numeric, ``target``
    falls back to that value and ``tolerance`` to ``ref.tolerance`` — but
    ``json_path`` must always be supplied via the dict form (otherwise we
    cannot know which field to read).
    """
    cfg = _cfg(ref)
    json_path = str(cfg.get("json_path", "")).strip()
    if not json_path:
        return False, "reference answer must provide non-empty 'json_path'"

    if "target" in cfg:
        try:
            target = float(cfg["target"])
        except (TypeError, ValueError):
            return False, "'target' must be numeric"
    elif isinstance(ref.value, (int, float)):
        target = float(ref.value)
    else:
        return False, "missing numeric 'target' (set value.target or ref.value)"

    if "tolerance" in cfg:
        try:
            tolerance = float(cfg["tolerance"])
        except (TypeError, ValueError):
            return False, "'tolerance' must be numeric"
    elif ref.tolerance is not None:
        tolerance = float(ref.tolerance)
    else:
        return False, "missing 'tolerance' (set value.tolerance or ref.tolerance)"

    return check_answer_json_numeric(
        answer,
        json_path=json_path,
        target=target,
        tolerance=tolerance,
    )


def check_stru_file_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Wire ``stru_file_check`` verifier from evidence + reference answer."""
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    filename = str(cfg.get("filename", ""))
    check_type = str(cfg.get("check", ""))
    expected = cfg.get("expected")
    if not filename or not check_type:
        return False, "stru_file_check: need 'filename' and 'check' in ref"
    min_sites = cfg.get("min_sites", 2)
    return check_stru_file(
        ws,
        filename=filename,
        check=check_type,
        expected=expected,
        min_sites=min_sites if isinstance(min_sites, int) else 2,
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


def check_abacus_input_from_evidence(
    *, evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Wire ``abacus_input_check`` verifier from evidence + reference answer."""
    ws, err = _get_workspace(evidence)
    if err:
        return False, err
    cfg = _cfg(ref)
    filename = str(cfg.get("filename", ""))
    check_type = str(cfg.get("check", ""))
    expected = cfg.get("expected")
    if not filename or not check_type:
        return False, "abacus_input_check: need 'filename' and 'check' in ref"
    return check_abacus_input(
        ws,
        filename=filename,
        check=check_type,
        expected=expected,
        workspace_resolve=_workspace_resolve_from_ref(ref),
    )


# Re-export from evaluator_json_checks to keep import paths stable
from .evaluator_json_checks import (  # noqa: E402, F401
    check_json_file_artifacts,
    check_json_file_numeric_range,
    check_json_file_schema,
)
