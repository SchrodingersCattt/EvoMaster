"""Tests for ``evaluation.core.slice_parser``."""

from __future__ import annotations

import pytest

from evaluation.core.slice_parser import parse_slices_expression


def test_single_capability_any_domain() -> None:
    out = parse_slices_expression('workflow_orchestration')
    assert len(out) == 1
    assert out[0].capability == 'workflow_orchestration'
    assert out[0].domains is None


def test_or_of_three_space_separated() -> None:
    out = parse_slices_expression('A B[a,b] C[d]')
    assert len(out) == 3
    assert out[0].capability == 'A' and out[0].domains is None
    assert out[1].capability == 'B' and out[1].domains == ['a', 'b']
    assert out[2].capability == 'C' and out[2].domains == ['d']


def test_rejects_empty() -> None:
    with pytest.raises(ValueError, match='empty'):
        parse_slices_expression('')


def test_rejects_empty_domain_brackets() -> None:
    with pytest.raises(ValueError, match='empty domain'):
        parse_slices_expression('cap[]')


def test_multiple_spaces_between_slices_ok() -> None:
    out = parse_slices_expression('A    B')
    assert len(out) == 2
    assert out[0].capability == 'A'
    assert out[1].capability == 'B'


def test_rejects_space_inside_brackets() -> None:
    with pytest.raises(ValueError, match='space'):
        parse_slices_expression('B[a, b]')


def test_rejects_space_inside_brackets_no_comma() -> None:
    with pytest.raises(ValueError, match='space'):
        parse_slices_expression('B[a b]')
