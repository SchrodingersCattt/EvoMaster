"""
Unit tests for pxrd-refinement cell standardisation helpers.

Run from project root:
  uv run pytest matmaster/skills/playground-skills/pxrd-refinement/tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _SKILL_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from gsas2_pawley import (  # noqa: E402
    cell_to_lattice,
    cell_volume,
    lattice_to_cell,
    merge_chain_directions,
    niggli_reduce_cell,
    standardize_cell,
)


def _assert_cell_close(actual: list[float], expected: list[float], tol: float = 1e-4):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert got == pytest.approx(want, abs=tol)


def test_cell_lattice_roundtrip_preserves_low_symmetry_cell():
    cell = [5.1, 6.2, 7.3, 77.0, 88.5, 103.2]

    roundtripped = lattice_to_cell(cell_to_lattice(cell))

    _assert_cell_close(roundtripped, cell)
    assert cell_volume(roundtripped) == pytest.approx(cell_volume(cell), abs=1e-8)


def test_standardize_cell_aligns_monoclinic_alternate_setting_to_reference():
    ref = [10.83, 9.59, 10.13, 90.0, 108.8, 90.0]
    result = {
        "a": 10.14,
        "b": 9.60,
        "c": 10.85,
        "alpha": 90.0,
        "beta": 71.3,
        "gamma": 90.0,
        "volume": 1000.0,
    }

    standardize_cell(result, ref_cell=ref, niggli=False)

    assert result["a"] == pytest.approx(10.85, abs=0.01)
    assert result["b"] == pytest.approx(9.60, abs=0.01)
    assert result["c"] == pytest.approx(10.14, abs=0.01)
    assert result["alpha"] == pytest.approx(90.0, abs=0.01)
    assert result["beta"] == pytest.approx(108.7, abs=0.01)
    assert result["gamma"] == pytest.approx(90.0, abs=0.01)


def test_standardize_cell_aligns_orthorhombic_axis_permutation_to_reference():
    ref = [5.0, 7.0, 9.0, 90.0, 90.0, 90.0]
    result = {
        "a": 9.01,
        "b": 5.02,
        "c": 7.03,
        "alpha": 90.0,
        "beta": 90.0,
        "gamma": 90.0,
        "volume": 315.0,
    }

    standardize_cell(result, ref_cell=ref, niggli=False)

    assert result["a"] == pytest.approx(5.02, abs=0.01)
    assert result["b"] == pytest.approx(7.03, abs=0.01)
    assert result["c"] == pytest.approx(9.01, abs=0.01)
    assert result["alpha"] == pytest.approx(90.0, abs=0.01)
    assert result["beta"] == pytest.approx(90.0, abs=0.01)
    assert result["gamma"] == pytest.approx(90.0, abs=0.01)


def test_spglib_niggli_reduce_preserves_volume():
    pytest.importorskip("spglib")
    cell = [10.83, 9.59, 10.13, 90.0, 108.8, 90.0]

    reduced = niggli_reduce_cell(cell)

    assert reduced != cell
    assert cell_volume(reduced) == pytest.approx(cell_volume(cell), abs=1e-8)


def test_standardize_cell_can_use_niggli_before_reference_alignment():
    pytest.importorskip("spglib")
    ref = [5.0, 6.0, 7.0, 80.0, 85.0, 75.0]
    result = {
        "a": 7.01,
        "b": 5.02,
        "c": 6.03,
        "alpha": 95.0,
        "beta": 100.0,
        "gamma": 75.1,
        "volume": 200.0,
    }

    standardize_cell(result, ref_cell=ref, niggli=True)

    assert result["a"] == pytest.approx(5.02, abs=0.01)
    assert result["b"] == pytest.approx(6.03, abs=0.01)
    assert result["c"] == pytest.approx(7.01, abs=0.01)
    assert result["alpha"] == pytest.approx(75.1, abs=0.01)
    assert result["beta"] == pytest.approx(85.0, abs=0.01)
    assert result["gamma"] == pytest.approx(80.0, abs=0.01)


def test_merge_chain_directions_prefers_reference_volume_for_high_wr_tie():
    reference_volume = 999.3806
    forward = [
        {"success": True, "file": "pxrd_303K.xy", "wR": 16.18, "volume": 997.6932},
        {"success": True, "file": "pxrd_323K.xy", "wR": 17.63, "volume": 998.6489},
        {"success": True, "file": "pxrd_343K.xy", "wR": 3.67, "volume": 1004.1638},
        {"success": True, "file": "pxrd_363K.xy", "wR": 3.66, "volume": 1006.1947},
    ]
    reverse = [
        {"success": True, "file": "pxrd_303K.xy", "wR": 16.12, "volume": 1002.5346},
        {"success": True, "file": "pxrd_323K.xy", "wR": 16.27, "volume": 1001.8401},
        {"success": True, "file": "pxrd_343K.xy", "wR": 3.67, "volume": 1004.0133},
        {"success": True, "file": "pxrd_363K.xy", "wR": 3.67, "volume": 1006.3714},
    ]

    merged, audit = merge_chain_directions(forward, reverse, reference_volume)

    assert [r["merge_source"] for r in merged] == [
        "forward",
        "forward",
        "forward",
        "forward",
    ]
    assert merged[0]["volume"] == pytest.approx(997.6932)
    assert merged[1]["volume"] == pytest.approx(998.6489)
    assert audit["reference_volume"] == pytest.approx(999.3806, abs=1e-4)
    assert audit["table"][0]["reason"].startswith("both high-wR/tied")
    assert "merged" not in audit
    assert "forward" not in audit
    assert "reverse" not in audit
