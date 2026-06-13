"""
Unit tests for pxrd-refinement cell standardisation helpers.

Run from project root:
  uv run pytest matmaster/skills/pxrd-refinement/tests/ -v
"""

from __future__ import annotations

import builtins
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


def test_cell_to_lattice_rejects_degenerate_gamma():
    with pytest.raises(ValueError, match="gamma=.*too close"):
        cell_to_lattice([5.0, 6.0, 7.0, 90.0, 90.0, 179.99999])


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


def test_standardize_cell_reorders_esds_from_explicit_axis_permutation():
    ref = [5.0, 7.0, 9.0, 90.0, 92.0, 88.0]
    result = {
        "a": 7.0,
        "b": 5.0,
        "c": 9.0,
        "alpha": 90.0,
        "beta": 88.0,
        "gamma": 92.0,
        "volume": 315.0,
        "a_esd": 0.07,
        "b_esd": 0.05,
        "c_esd": 0.09,
        "alpha_esd": 0.90,
        "beta_esd": 0.88,
        "gamma_esd": 0.92,
    }

    standardize_cell(result, ref_cell=ref, niggli=False)

    assert [
        result[field]
        for field in (
            "a_esd",
            "b_esd",
            "c_esd",
            "alpha_esd",
            "beta_esd",
            "gamma_esd",
        )
    ] == [0.05, 0.07, 0.09, 0.90, 0.92, 0.88]


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


def test_niggli_reduce_cell_records_missing_spglib_warning(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "spglib":
            raise ImportError("no spglib in test")
        return real_import(name, *args, **kwargs)

    warnings: list[str] = []
    monkeypatch.setattr(builtins, "__import__", fake_import)

    cell = [10.83, 9.59, 10.13, 90.0, 108.8, 90.0]
    assert niggli_reduce_cell(cell, warnings_out=warnings) == cell
    assert warnings == ["spglib not available; Niggli reduction skipped"]


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
    # Both candidates within 1% of reference_volume → no off-ref warning.
    assert audit["warnings"] == []
    assert all(row["warning"] is None for row in audit["table"])


def test_merge_chain_directions_rejects_length_mismatch():
    forward = [{"success": True, "file": "a.xy", "wR": 1.0, "volume": 100.0}]
    reverse = [
        {"success": True, "file": "a.xy", "wR": 1.0, "volume": 100.0},
        {"success": True, "file": "b.xy", "wR": 1.0, "volume": 101.0},
    ]

    with pytest.raises(ValueError, match="lengths differ"):
        merge_chain_directions(forward, reverse, reference_volume=100.0)


def test_merge_warns_when_both_directions_off_reference_volume():
    """Reproduces the r2 303K wrong-basin scenario: both fwd and rev are high-wR
    *and* > 1% off reference volume → the merge picks the lesser-evil candidate
    but flags the pattern in `merge_audit.warnings` so the agent can re-refine.
    """
    reference_volume = 999.3806
    forward = [
        {"success": True, "file": "pxrd_303K.xy", "wR": 14.15, "volume": 980.2117},
        {"success": True, "file": "pxrd_323K.xy", "wR": 3.66, "volume": 998.66},
    ]
    reverse = [
        {"success": True, "file": "pxrd_303K.xy", "wR": 14.23, "volume": 972.4012},
        {"success": True, "file": "pxrd_323K.xy", "wR": 3.50, "volume": 998.50},
    ]

    merged, audit = merge_chain_directions(forward, reverse, reference_volume)

    assert merged[0]["merge_source"] == "forward"
    assert merged[0]["merge_warning"] == "both_directions_off_ref"
    assert audit["table"][0]["warning"] == "both_directions_off_ref"
    assert audit["table"][1]["warning"] is None
    assert len(audit["warnings"]) == 1
    flag = audit["warnings"][0]
    assert flag["file"] == "pxrd_303K.xy"
    assert flag["issue"] == "both_directions_off_ref"
    assert flag["wR_forward"] == pytest.approx(14.15)
    assert flag["dV_ref_forward"] == pytest.approx(19.169, abs=1e-2)


def test_merge_does_not_warn_when_low_wr_even_if_volume_diverges_from_ref():
    """A clean, low-wR refinement that lands far from the seed cell is the user's
    reference being off (e.g. unexpected expansion), not a basin failure. No warning.
    """
    reference_volume = 1000.0
    forward = [
        {"success": True, "file": "p.xy", "wR": 3.10, "volume": 1025.0},
    ]
    reverse = [
        {"success": True, "file": "p.xy", "wR": 3.05, "volume": 1024.5},
    ]

    _, audit = merge_chain_directions(forward, reverse, reference_volume)

    assert audit["warnings"] == []
    assert audit["table"][0]["warning"] is None


def _ref_cell_303K() -> list[float]:
    return [10.83, 9.62, 10.13, 90.0, 108.75, 90.0]


def test_merge_picks_closer_reference_cell_when_volumes_match():
    """Reproduces the audit-run r0/r1 303K basin trap: forward and reverse both
    have V ≈ V_ref but reverse converged to a wildly different `a` (10.98 vs
    target 10.83). V proximity alone would pick reverse (V closer); the
    cell-distance tiebreak picks forward (cell closer).
    """
    ref_cell = _ref_cell_303K()
    forward = [
        {
            "success": True,
            "file": "pxrd_303K.xy",
            "wR": 16.18,
            "volume": 997.6942,
            "a": 10.8199,
            "b": 9.5512,
            "c": 10.1568,
            "alpha": 90.0,
            "beta": 108.75,
            "gamma": 90.0,
        },
    ]
    reverse = [
        {
            "success": True,
            "file": "pxrd_303K.xy",
            "wR": 16.14,
            "volume": 998.9899,
            "a": 10.9803,
            "b": 9.4500,
            "c": 10.0500,
            "alpha": 90.0,
            "beta": 108.629,
            "gamma": 90.0,
        },
    ]

    merged, audit = merge_chain_directions(
        forward, reverse, reference_volume=999.3806, reference_cell=ref_cell
    )

    assert merged[0]["merge_source"] == "forward"
    assert audit["table"][0]["reason"].endswith("reference cell")
    assert (
        audit["table"][0]["cell_dist_forward"] < audit["table"][0]["cell_dist_reverse"]
    )
    assert audit["reference_cell"] == [10.83, 9.62, 10.13, 90.0, 108.75, 90.0]


def test_merge_warns_when_axes_diverge_even_if_volume_matches():
    """V matches reference within 0.5% on both directions but `a` differs by
    > 1%; the picker still selects the closer cell, AND the audit must flag
    the pattern so the agent does not silently report the wrong-basin solution.
    """
    ref_cell = _ref_cell_303K()
    forward = [
        {
            "success": True,
            "file": "pxrd_303K.xy",
            "wR": 16.18,
            "volume": 997.6942,
            "a": 10.8199,
            "b": 9.5512,
            "c": 10.1568,
            "alpha": 90.0,
            "beta": 108.75,
            "gamma": 90.0,
        },
    ]
    reverse = [
        {
            "success": True,
            "file": "pxrd_303K.xy",
            "wR": 16.14,
            "volume": 998.9899,
            "a": 10.9803,
            "b": 9.4500,
            "c": 10.0500,
            "alpha": 90.0,
            "beta": 108.629,
            "gamma": 90.0,
        },
    ]

    _, audit = merge_chain_directions(
        forward, reverse, reference_volume=999.3806, reference_cell=ref_cell
    )

    assert len(audit["warnings"]) == 1
    flag = audit["warnings"][0]
    assert flag["issue"] == "both_directions_off_ref"
    assert flag["cell_dist_forward"] is not None
    assert flag["cell_dist_reverse"] is not None
    assert flag["cell_dist_reverse"] > flag["cell_dist_forward"]


def test_merge_falls_back_to_volume_when_no_reference_cell():
    """Backward-compatible path: when the caller does not supply
    reference_cell (e.g. a programmatic invocation that only knows V_ref),
    the picker still uses V proximity for high-wR ties.
    """
    forward = [
        {
            "success": True,
            "file": "p.xy",
            "wR": 16.0,
            "volume": 999.0,
            "a": 10.82,
            "b": 9.55,
            "c": 10.16,
            "alpha": 90.0,
            "beta": 108.75,
            "gamma": 90.0,
        },
    ]
    reverse = [
        {
            "success": True,
            "file": "p.xy",
            "wR": 16.0,
            "volume": 1004.0,
            "a": 10.82,
            "b": 9.55,
            "c": 10.16,
            "alpha": 90.0,
            "beta": 108.75,
            "gamma": 90.0,
        },
    ]

    merged, audit = merge_chain_directions(forward, reverse, reference_volume=1000.0)

    assert merged[0]["merge_source"] == "forward"
    assert "reference_cell" not in audit
    assert audit["table"][0]["cell_dist_forward"] is None
    assert audit["table"][0]["cell_dist_reverse"] is None
    assert audit["table"][0]["reason"].endswith("reference volume")
