"""
Unit tests for the post-chain self-healing helper.

These tests do NOT touch GSAS-II; they exercise the outlier-detection branching
inside ``self_heal_chain_outliers`` by feeding synthetic chain results whose
``file`` paths do not exist.  In that case the helper records the outlier in
the audit but skips the actual retry (decision="skipped_no_file"), which is
exactly what we want to assert: detection logic is independent of the GSAS-II
backend.

Run from project root:
  uv run pytest matmaster/skills/playground-skills/pxrd-refinement/tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_SKILL_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _SKILL_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from gsas2_pawley import self_heal_chain_outliers  # noqa: E402


def _result(file: str, V: float, a: float = 10.83, success: bool = True) -> dict:
    return {
        "success": success,
        "file": file,
        "a": a,
        "b": 9.62,
        "c": 10.13,
        "alpha": 90.0,
        "beta": 108.76,
        "gamma": 90.0,
        "volume": V,
        "wR": 5.0,
    }


def _stub_args() -> SimpleNamespace:
    return SimpleNamespace(
        space_group="P 21",
        wavelength=1.5406,
        instprm=None,
        dmin=2.0,
        dmax=None,
        tmin=14.0,
        tmax=50.0,
        debug_plot=None,
        curation_mode="auto",
        baseline_method="piecewise_linear",
        multi_start=1,
        multi_start_seed=42,
        multi_start_len_sigma=0.005,
        multi_start_ang_sigma=0.5,
        standardize_cell=None,
        chain_cell=True,
        chain_cell_direction="both",
        chain_wr_max=25.0,
        chain_vol_jump_max=0.05,
        self_heal_chain=True,
        self_heal_v_jump_threshold=0.02,
        self_heal_multi_start=5,
    )


REF_CELL = [10.83, 9.62, 10.13, 90.0, 108.76, 90.0]
REF_VOL = 1000.0


def test_short_chain_skipped() -> None:
    chain = [_result("/missing/a.xy", 998.0), _result("/missing/b.xy", 1001.0)]
    healed, audit = self_heal_chain_outliers(
        chain,
        args=_stub_args(),
        reference_cell=REF_CELL,
        reference_volume=REF_VOL,
        v_jump_threshold=0.02,
        multi_start=5,
    )
    assert healed == chain
    assert audit["outliers"] == []
    assert "skipped_reason" in audit


def test_no_outlier_no_action() -> None:
    chain = [
        _result("/missing/a.xy", 998.0),
        _result("/missing/b.xy", 1001.0),
        _result("/missing/c.xy", 1005.0),
        _result("/missing/d.xy", 1007.0),
    ]
    healed, audit = self_heal_chain_outliers(
        chain,
        args=_stub_args(),
        reference_cell=REF_CELL,
        reference_volume=REF_VOL,
        v_jump_threshold=0.02,
        multi_start=5,
    )
    assert healed == chain
    assert audit["outliers"] == []


def test_detect_outlier_records_audit() -> None:
    """The 323K-style 2.8% drop should be flagged."""
    chain = [
        _result("/missing/303.xy", 994.98),
        _result("/missing/323.xy", 977.58),
        _result("/missing/343.xy", 1005.32),
        _result("/missing/363.xy", 1007.27),
    ]
    healed, audit = self_heal_chain_outliers(
        chain,
        args=_stub_args(),
        reference_cell=REF_CELL,
        reference_volume=REF_VOL,
        v_jump_threshold=0.02,
        multi_start=5,
    )
    assert audit["v_jump_threshold"] == 0.02
    assert audit["multi_start"] == 5
    flagged = [o for o in audit["outliers"] if "323.xy" in o["file"]]
    assert len(flagged) == 1
    row = flagged[0]
    assert row["v_original"] == 977.58
    assert abs(row["v_neighbour_target"] - (994.98 + 1005.32) / 2) < 1e-3
    assert row["rel_jump"] > 0.02
    assert row["decision"] == "skipped_no_file"
    assert healed == chain


def test_threshold_under_3pct_not_triggered_at_5pct() -> None:
    """A 2.8% drop should NOT trigger when the threshold is set to 5%."""
    chain = [
        _result("/missing/303.xy", 994.98),
        _result("/missing/323.xy", 977.58),
        _result("/missing/343.xy", 1005.32),
        _result("/missing/363.xy", 1007.27),
    ]
    healed, audit = self_heal_chain_outliers(
        chain,
        args=_stub_args(),
        reference_cell=REF_CELL,
        reference_volume=REF_VOL,
        v_jump_threshold=0.05,
        multi_start=5,
    )
    assert audit["outliers"] == []
    assert healed == chain


def test_failed_chain_element_not_flagged() -> None:
    chain = [
        _result("/missing/a.xy", 998.0),
        {"success": False, "file": "/missing/b.xy", "error": "boom"},
        _result("/missing/c.xy", 1005.0),
    ]
    healed, audit = self_heal_chain_outliers(
        chain,
        args=_stub_args(),
        reference_cell=REF_CELL,
        reference_volume=REF_VOL,
        v_jump_threshold=0.02,
        multi_start=5,
    )
    assert audit["outliers"] == []
    assert healed == chain
