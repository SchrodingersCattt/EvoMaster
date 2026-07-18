"""Tests for the receipt-backed ``bohr_cli_operation_invoked`` grounding check."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from evaluation.core.evidence import BohrCliReceiptRecord
from evaluation.core.schemas import QuestionItem, ReferenceAnswer, ScoringCheckItem
from evaluation.validators.bohr_cli import check_bohr_cli_operation_invoked


def _receipt(operation: str, *, ok: bool = True, **kwargs) -> BohrCliReceiptRecord:
    return BohrCliReceiptRecord(
        schema_version="bohr_cli_receipt_v1",
        operation=operation,
        exit_code=0 if ok else 1,
        ok=ok,
        **kwargs,
    )


def test_exact_operation_match_requires_success_by_default() -> None:
    receipts = [_receipt("pdf.parse", ok=True)]
    passed, reason = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["pdf.parse"]
    )
    assert passed is True
    assert "matched=1" in reason


def test_failed_operation_rejected_when_require_ok() -> None:
    receipts = [_receipt("pdf.parse", ok=False)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["pdf.parse"]
    )
    assert passed is False


def test_failed_operation_accepted_when_require_ok_false() -> None:
    # mentor errors server-side but the invocation is the grounding signal.
    receipts = [_receipt("mentor.some question text", ok=False)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["mentor"], require_ok=False
    )
    assert passed is True


def test_bare_noun_prefix_matches_positional_argument_operation() -> None:
    # `bohr mentor "<q>"` is parsed as operation `mentor.<q>`; a bare `mentor`
    # entry must still match via prefix.
    receipts = [_receipt("mentor.how do edge effects work", ok=False)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["mentor"], require_ok=False
    )
    assert passed is True


def test_qualified_entry_does_not_over_match() -> None:
    # `pdf.parse` must not match an unrelated `pdf.parser`-style operation.
    receipts = [_receipt("pdf.parserino", ok=True)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["pdf.parse"]
    )
    assert passed is False


def test_synonym_operations_any_of_allow_list() -> None:
    # Either paper.search or lkm.search grounds the literature-search step.
    receipts = [_receipt("lkm.search", ok=True)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["paper.search", "lkm.search"]
    )
    assert passed is True


def test_help_and_dry_run_never_count() -> None:
    receipts = [
        _receipt("pdf.parse", ok=True, help_requested=True),
        _receipt("pdf.parse", ok=True, dry_run=True),
    ]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["pdf.parse"]
    )
    assert passed is False


def test_min_matches_enforced() -> None:
    receipts = [_receipt("lkm.search", ok=True)]
    passed, _ = check_bohr_cli_operation_invoked(
        receipts=receipts, operations=["lkm.search"], min_matches=2
    )
    assert passed is False


def _question(value: object) -> None:
    QuestionItem(
        id="op_invoked_ref_test",
        capability="scientific_analysis",
        domain="agnostic",
        intent="Ground a step on execution receipts.",
        human_prompt_seed="Search the literature.",
        reference_answers=[ReferenceAnswer(key="searched", value=value)],
        scoring_checklist=[
            ScoringCheckItem(
                id="searched",
                criterion="Search was invoked.",
                axis="grounding",
                verify="bohr_cli_operation_invoked",
            )
        ],
    )


def test_reference_requires_operations_list() -> None:
    _question({"operations": ["lkm.search"], "min_matches": 1})  # valid
    for bad in (
        {"min_matches": 1},
        {"operations": []},
        {"operations": [""]},
        {"operations": "lkm.search", "unknown": 1},
        {"operations": ["lkm.search"], "min_matches": 0},
    ):
        with pytest.raises(
            PydanticValidationError, match="bohr_cli_operation_invoked reference"
        ):
            _question(bad)
