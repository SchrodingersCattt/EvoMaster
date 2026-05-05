"""Regression tests for the Pawley multi-start picker.

These exercise ``gsas2_pawley.pick_best_candidate`` and
``gsas2_pawley.summarize_multi_start`` against real-data fixtures captured
from two different evaluation runs of the same task
(``PXRD_thermal_expansion_001_20260502_v4``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = (
    _REPO_ROOT
    / "matmaster"
    / "skills"
    / "playground-skills"
    / "pxrd-refinement"
    / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

pytest.importorskip("numpy")  # skip gracefully when numpy is absent
from gsas2_pawley import (  # noqa: E402
    COLD_START_WR_FLOOR,
    COLD_START_WR_SPREAD,
    REF_VOL_WR_TOLERANCE,
    pick_best_candidate,
    summarize_multi_start,
)


def _candidate(
    seed_index: int,
    *,
    wR: float,
    a: float,
    b: float,
    c: float,
    beta: float,
    volume: float,
    seed_cell: list[float] | None = None,
    success: bool = True,
    error: str | None = None,
) -> dict:
    """Build a fake Pawley candidate matching ``run_pawley_once`` output."""
    cand: dict = {
        "_seed_index": seed_index,
        "_seed_cell": seed_cell or [a, b, c, 90.0, beta, 90.0],
        "success": success,
        "wR": wR,
        "warnings": [],
    }
    if success:
        cand.update(
            {
                "a": round(a, 5),
                "b": round(b, 5),
                "c": round(c, 5),
                "alpha": 90.0,
                "beta": round(beta, 4),
                "gamma": 90.0,
                "volume": round(volume, 4),
                "n_reflections": 200,
            }
        )
    else:
        cand["error"] = error or "synthetic failure"
    return cand


# ---------------------------------------------------------------------------
# Real fixtures from runs of PXRD_thermal_expansion_001_20260502_v4
# ---------------------------------------------------------------------------

T303K_COLD_START_FAILING = [
    _candidate(0, wR=15.61, a=10.83, b=9.62, c=10.13, beta=108.75, volume=982.38),
    _candidate(
        1, wR=15.86, a=10.84651, b=9.57011, c=10.16808, beta=107.77448, volume=998.50
    ),
    _candidate(
        2, wR=15.53, a=10.83692, b=9.6048, c=10.12915, beta=109.1897, volume=939.00
    ),
]

T303K_COLD_START_PASSING = [
    _candidate(
        0, wR=15.47, a=10.81382, b=9.60639, c=10.11319, beta=108.7214, volume=994.99
    ),
    _candidate(1, wR=16.18, a=10.80, b=9.57, c=10.16, beta=107.77, volume=997.70),
    _candidate(2, wR=15.59, a=10.81, b=9.60, c=10.13, beta=109.19, volume=938.20),
    _candidate(3, wR=16.09, a=10.81, b=9.67, c=10.15, beta=108.93, volume=980.26),
    _candidate(4, wR=16.04, a=10.85, b=9.62, c=10.12, beta=109.36, volume=970.16),
]

T110C_HOT_REGIME = [
    _candidate(
        0, wR=2.80, a=11.04801, b=9.47885, c=10.47869, beta=111.2762, volume=1022.56
    ),
    _candidate(
        1, wR=5.28, a=11.06685, b=9.43083, c=10.5194, beta=110.27448, volume=1043.45
    ),
    _candidate(
        2, wR=2.81, a=11.05707, b=9.46502, c=10.47912, beta=111.6897, volume=1021.79
    ),
    _candidate(
        3, wR=2.95, a=11.05365, b=9.53358, c=10.50453, beta=111.43438, volume=1018.24
    ),
    _candidate(
        4, wR=3.27, a=11.09864, b=9.47763, c=10.47032, beta=111.86127, volume=1018.11
    ),
]


class TestPickBestCandidateColdStart:
    def test_failing_run_t303k_picks_seed0_not_min_wr(self):
        picked, reason = pick_best_candidate(T303K_COLD_START_FAILING)
        assert picked["_seed_index"] == 0
        assert picked["volume"] == pytest.approx(982.38, abs=0.01)
        assert "cold-start tiebreak" in reason
        assert "seed_index=0" in reason

    def test_passing_run_t303k_also_picks_seed0(self):
        picked, reason = pick_best_candidate(T303K_COLD_START_PASSING)
        assert picked["_seed_index"] == 0
        assert picked["volume"] == pytest.approx(994.99, abs=0.01)
        assert "cold-start tiebreak" in reason

    def test_cold_start_thresholds_are_documented_constants(self):
        assert COLD_START_WR_FLOOR == 10.0
        assert COLD_START_WR_SPREAD == 1.5


class TestPickBestCandidateHotRegime:
    def test_t110c_picks_min_wr_seed0(self):
        picked, reason = pick_best_candidate(T110C_HOT_REGIME)
        assert picked["_seed_index"] == 0
        assert picked["wR"] == 2.80
        assert "min-wR" in reason
        assert "cold-start" not in reason

    def test_min_wr_wins_when_seed_other_than_zero_is_best(self):
        candidates = [
            _candidate(0, wR=4.5, a=11.0, b=9.5, c=10.5, beta=111.0, volume=1020.0),
            _candidate(1, wR=2.8, a=11.05, b=9.48, c=10.48, beta=111.27, volume=1023.0),
            _candidate(2, wR=3.5, a=11.04, b=9.47, c=10.47, beta=111.30, volume=1019.0),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 1
        assert "min-wR" in reason


class TestPickBestCandidateBoundary:
    def test_below_wr_floor_does_not_trigger_tiebreak(self):
        candidates = [
            _candidate(0, wR=9.50, a=10.83, b=9.62, c=10.13, beta=108.75, volume=982.0),
            _candidate(1, wR=9.40, a=10.84, b=9.57, c=10.17, beta=107.77, volume=998.0),
            _candidate(2, wR=9.60, a=10.84, b=9.60, c=10.13, beta=109.19, volume=939.0),
        ]
        picked, _ = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 1

    def test_wide_spread_does_not_trigger_tiebreak(self):
        candidates = [
            _candidate(0, wR=15.0, a=10.83, b=9.62, c=10.13, beta=108.75, volume=982.0),
            _candidate(1, wR=18.0, a=10.84, b=9.57, c=10.17, beta=107.77, volume=998.0),
            _candidate(2, wR=12.0, a=10.84, b=9.60, c=10.13, beta=109.19, volume=939.0),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 2
        assert "min-wR" in reason

    def test_just_inside_cold_start_window_triggers(self):
        candidates = [
            _candidate(0, wR=10.5, a=10.83, b=9.62, c=10.13, beta=108.75, volume=982.0),
            _candidate(1, wR=11.0, a=10.84, b=9.57, c=10.17, beta=107.77, volume=998.0),
            _candidate(2, wR=10.4, a=10.84, b=9.60, c=10.13, beta=109.19, volume=939.0),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 0
        assert "cold-start tiebreak" in reason


class TestPickBestCandidateFailures:
    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            pick_best_candidate([])

    def test_all_failed_returns_first_with_reason(self):
        candidates = [
            _candidate(
                0,
                wR=0.0,
                a=0,
                b=0,
                c=0,
                beta=0,
                volume=0,
                success=False,
                error="GSAS-II crashed",
            ),
            _candidate(
                1,
                wR=0.0,
                a=0,
                b=0,
                c=0,
                beta=0,
                volume=0,
                success=False,
                error="GSAS-II crashed",
            ),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 0
        assert "all candidates failed" in reason

    def test_single_success_uses_it_directly(self):
        candidates = [
            _candidate(
                0,
                wR=0.0,
                a=0,
                b=0,
                c=0,
                beta=0,
                volume=0,
                success=False,
                error="GSAS-II crashed",
            ),
            _candidate(1, wR=15.5, a=10.84, b=9.57, c=10.17, beta=107.77, volume=998.0),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 1
        assert "only successful seed" in reason

    def test_seed0_failed_falls_through_to_min_wr(self):
        candidates = [
            _candidate(
                0,
                wR=0.0,
                a=0,
                b=0,
                c=0,
                beta=0,
                volume=0,
                success=False,
                error="GSAS-II crashed",
            ),
            _candidate(
                1,
                wR=15.86,
                a=10.84651,
                b=9.57011,
                c=10.16808,
                beta=107.77448,
                volume=998.50,
            ),
            _candidate(
                2,
                wR=15.53,
                a=10.83692,
                b=9.6048,
                c=10.12915,
                beta=109.1897,
                volume=939.00,
            ),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 2
        assert "min-wR" in reason


class TestPickBestCandidateAnchor:
    def test_anchor_filters_out_sqrt2_trap(self):
        candidates = [
            _candidate(0, wR=3.20, a=10.9, b=9.6, c=10.1, beta=108.8, volume=1010.0),
            _candidate(1, wR=2.80, a=10.7, b=9.2, c=10.0, beta=108.8, volume=959.0),
            _candidate(2, wR=3.00, a=10.9, b=9.7, c=10.1, beta=108.8, volume=1015.0),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            anchor_volume=1006.0,
            anchor_max_jump=0.03,
        )
        assert picked["volume"] == pytest.approx(1015.0, abs=0.01)
        assert picked["_seed_index"] == 2
        assert "anchor V=1006.00" in reason
        assert "1 rejected" in reason

    def test_anchor_falls_through_when_no_survivors(self):
        candidates = [
            _candidate(0, wR=4.5, a=10.8, b=9.6, c=10.1, beta=108.8, volume=1000.0),
            _candidate(1, wR=3.1, a=10.9, b=9.7, c=10.2, beta=108.8, volume=1010.0),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            anchor_volume=1500.0,
            anchor_max_jump=0.03,
        )
        assert picked["_seed_index"] == 1
        assert "anchor V=1500.00 rejected all seeds" in reason
        assert "min-wR" in reason

    def test_anchor_none_is_backwards_compatible(self):
        picked, reason = pick_best_candidate(
            T303K_COLD_START_FAILING,
            anchor_volume=None,
        )
        assert picked["_seed_index"] == 0
        assert "cold-start tiebreak" in reason
        assert "anchor V=" not in reason

    def test_anchor_combines_with_cold_start_and_rejects_seed0(self):
        candidates = [
            _candidate(0, wR=15.20, a=10.7, b=9.2, c=10.0, beta=108.8, volume=900.0),
            _candidate(1, wR=15.80, a=10.8, b=9.6, c=10.1, beta=108.8, volume=998.0),
            _candidate(2, wR=15.40, a=10.8, b=9.6, c=10.1, beta=108.8, volume=1001.0),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            anchor_volume=999.0,
            anchor_max_jump=0.03,
        )
        assert picked["_seed_index"] == 2
        assert picked["volume"] == pytest.approx(1001.0, abs=0.01)
        assert "anchor V=999.00" in reason
        assert "1 rejected" in reason
        assert "min-wR" in reason


class TestPickBestCandidateRefVol:
    """Reference-volume proximity — used for the first chain point (no anchor)."""

    def test_ref_vol_prefers_closer_seed_over_min_wr(self):
        """Real 303K scenario: seed 3 has min wR=6.8% but V=1002.68 far from
        ref V≈999; seed 4 has wR=8.61% but V=996.37 much closer."""
        candidates = [
            _candidate(
                0, wR=8.6, a=10.779, b=9.747, c=10.12, beta=108.37, volume=993.43
            ),
            _candidate(
                1, wR=9.69, a=10.902, b=9.575, c=10.02, beta=108.82, volume=1033.43
            ),
            _candidate(
                2, wR=8.65, a=10.779, b=9.614, c=10.10, beta=109.07, volume=990.71
            ),
            _candidate(
                3, wR=6.8, a=10.708, b=9.748, c=10.12, beta=108.37, volume=1002.68
            ),
            _candidate(
                4, wR=8.61, a=10.798, b=9.693, c=10.15, beta=109.24, volume=996.37
            ),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            reference_volume=999.38,
        )
        assert picked["_seed_index"] == 4
        assert "ref-vol proximity" in reason
        assert "min-wR=6.80%" in reason

    def test_ref_vol_no_effect_when_anchor_active(self):
        """When chain anchor is set, ref_vol proximity is skipped."""
        candidates = [
            _candidate(0, wR=3.5, a=10.87, b=9.63, c=10.14, beta=108.80, volume=1005.0),
            _candidate(1, wR=3.2, a=10.90, b=9.63, c=10.15, beta=108.80, volume=1009.0),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            anchor_volume=1007.0,
            anchor_max_jump=0.03,
            reference_volume=950.0,
        )
        assert picked["_seed_index"] == 1
        assert "min-wR" in reason
        assert "ref-vol" not in reason

    def test_ref_vol_no_effect_when_min_wr_is_closest(self):
        """When min-wR seed is also closest to reference, normal min-wR path."""
        candidates = [
            _candidate(0, wR=5.0, a=10.83, b=9.62, c=10.13, beta=108.75, volume=999.0),
            _candidate(1, wR=7.0, a=10.70, b=9.60, c=10.10, beta=108.50, volume=980.0),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            reference_volume=1000.0,
        )
        assert picked["_seed_index"] == 0
        assert "min-wR" in reason

    def test_ref_vol_respects_tolerance_boundary(self):
        """Seed outside the wR tolerance window is NOT considered."""
        candidates = [
            _candidate(0, wR=5.0, a=10.83, b=9.62, c=10.13, beta=108.75, volume=1010.0),
            _candidate(
                1,
                wR=5.0 + REF_VOL_WR_TOLERANCE + 0.1,
                a=10.83,
                b=9.62,
                c=10.13,
                beta=108.75,
                volume=999.0,
            ),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            reference_volume=1000.0,
        )
        assert picked["_seed_index"] == 0
        assert "min-wR" in reason

    def test_ref_vol_overrides_cold_start_seed0_preference(self):
        """In cold-start regime (all wR>10%, spread<1.5%) without anchor,
        ref-vol proximity should take priority over seed-0 fallback."""
        candidates = [
            _candidate(
                0, wR=15.61, a=10.785, b=9.58, c=10.05, beta=108.8, volume=982.4
            ),
            _candidate(
                1, wR=15.86, a=10.811, b=9.62, c=10.13, beta=108.75, volume=998.5
            ),
            _candidate(2, wR=15.53, a=10.657, b=9.50, c=9.90, beta=108.5, volume=939.0),
            _candidate(
                3, wR=15.88, a=10.723, b=9.55, c=10.00, beta=108.6, volume=971.3
            ),
            _candidate(
                4, wR=15.88, a=10.808, b=9.57, c=10.02, beta=108.7, volume=979.7
            ),
        ]
        picked, reason = pick_best_candidate(
            candidates,
            reference_volume=999.4,
        )
        assert picked["_seed_index"] == 1
        assert "ref-vol proximity" in reason
        assert picked["volume"] == pytest.approx(998.5, abs=0.1)

    def test_ref_vol_none_is_backwards_compatible(self):
        """reference_volume=None (default) doesn't change behavior."""
        candidates = [
            _candidate(
                0, wR=8.6, a=10.779, b=9.747, c=10.12, beta=108.37, volume=993.0
            ),
            _candidate(
                1, wR=6.8, a=10.708, b=9.748, c=10.12, beta=108.37, volume=1003.0
            ),
        ]
        picked, reason = pick_best_candidate(candidates)
        assert picked["_seed_index"] == 1
        assert "min-wR" in reason


class TestSummarizeMultiStart:
    def test_preserves_full_cell_for_successful_seeds(self):
        summary = summarize_multi_start(T303K_COLD_START_FAILING)
        assert len(summary) == 3
        for entry, expected in zip(summary, T303K_COLD_START_FAILING):
            assert entry["seed_index"] == expected["_seed_index"]
            assert entry["seed_cell"] == expected["_seed_cell"]
            assert entry["success"] is True
            assert entry["wR"] == expected["wR"]
            for key in ("a", "b", "c", "alpha", "beta", "gamma", "volume"):
                assert entry[key] == expected[key]

    def test_failing_seed_carries_error_not_cell(self):
        candidates = [
            _candidate(0, wR=15.5, a=10.83, b=9.62, c=10.13, beta=108.75, volume=982.0),
            _candidate(
                1,
                wR=0,
                a=0,
                b=0,
                c=0,
                beta=0,
                volume=0,
                success=False,
                error="refine step 'Cell' raised SVD",
            ),
        ]
        summary = summarize_multi_start(candidates)
        assert summary[0]["a"] == 10.83
        assert summary[0]["volume"] == 982.0
        assert "error" not in summary[0]
        assert summary[1]["success"] is False
        assert summary[1]["error"] == "refine step 'Cell' raised SVD"
        assert "a" not in summary[1]
        assert "volume" not in summary[1]

    def test_recovery_workflow_extracts_right_basin_when_picker_picked_wrong(self):
        summary = summarize_multi_start(T303K_COLD_START_FAILING)
        target_volume = 999.81
        best_match = min(
            (s for s in summary if s.get("success")),
            key=lambda s: abs(s["volume"] - target_volume),
        )
        assert best_match["seed_index"] == 1
        assert best_match["volume"] == pytest.approx(998.50, abs=0.01)
        assert best_match["a"] == pytest.approx(10.84651, abs=1e-4)
        assert best_match["beta"] == pytest.approx(107.77448, abs=1e-4)
