"""Unit tests for Pawley chain-cell traversal order and anchor threading."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "matmaster" / "skills" / "pxrd-refinement" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gsas2_pawley  # noqa: E402


def _write_patterns(tmp_path: Path) -> Path:
    data_dir = tmp_path / "patterns"
    data_dir.mkdir()
    for temp in (303, 323, 343, 363):
        (data_dir / f"pxrd_{temp}K.xye").write_text(
            "10.0 100.0\n11.0 120.0\n12.0 90.0\n",
            encoding="utf-8",
        )
    return data_dir


def _args(data_dir: Path, *, direction: str) -> argparse.Namespace:
    return argparse.Namespace(
        data=str(data_dir),
        instprm=None,
        wavelength=1.5406,
        space_group="P 1 21 1",
        cell="a=10,b=9,c=10,beta=108",
        dmin=2.0,
        dmax=None,
        tmin=None,
        tmax=None,
        debug_plot=None,
        curation_mode="off",
        baseline_method="piecewise_linear",
        multi_start=5,
        multi_start_seed=42,
        multi_start_len_sigma=0.005,
        multi_start_ang_sigma=0.5,
        chain_cell=True,
        chain_cell_direction=direction,
        chain_wr_max=25.0,
        chain_vol_jump_max=0.03,
    )


def _fake_refiner(call_log: list[dict]):
    volumes = {
        "pxrd_303K": 999.0,
        "pxrd_323K": 1002.0,
        "pxrd_343K": 1006.0,
        "pxrd_363K": 1012.0,
    }

    def fake_refine_one_pattern(**kwargs):
        label = kwargs["label"]
        call_log.append(
            {
                "label": label,
                "cell_list": list(kwargs["cell_list"]),
                "anchor_volume": kwargs.get("anchor_volume"),
                "anchor_max_jump": kwargs.get("anchor_max_jump"),
            }
        )
        volume = volumes[label]
        return {
            "success": True,
            "file": label,
            "a": 10.0 + (volume - 999.0) / 1000.0,
            "b": 9.0,
            "c": 10.0,
            "alpha": 90.0,
            "beta": 108.0,
            "gamma": 90.0,
            "volume": volume,
            "wR": 3.0,
            "n_reflections": 100,
            "warnings": [],
            "multi_start": [],
            "multi_start_pick": {"seed_index": 0, "reason": "synthetic"},
        }

    return fake_refine_one_pattern


def test_forward_chain_order_and_anchor_threading(tmp_path, monkeypatch):
    data_dir = _write_patterns(tmp_path)
    calls: list[dict] = []
    monkeypatch.setattr(gsas2_pawley, "refine_one_pattern", _fake_refiner(calls))

    result = gsas2_pawley.run_directory(_args(data_dir, direction="forward"))

    assert [c["label"] for c in calls] == [
        "pxrd_303K",
        "pxrd_323K",
        "pxrd_343K",
        "pxrd_363K",
    ]
    assert [c["anchor_volume"] for c in calls] == [None, 999.0, 1002.0, 1006.0]
    assert all(c["anchor_max_jump"] == 0.03 for c in calls)
    assert result["chain_cell_direction"] == "forward"
    assert [Path(r["file"]).stem for r in result["results"]] == [
        "pxrd_303K",
        "pxrd_323K",
        "pxrd_343K",
        "pxrd_363K",
    ]


def test_reverse_chain_order_with_canonical_result_sort(tmp_path, monkeypatch):
    data_dir = _write_patterns(tmp_path)
    calls: list[dict] = []
    monkeypatch.setattr(gsas2_pawley, "refine_one_pattern", _fake_refiner(calls))

    result = gsas2_pawley.run_directory(_args(data_dir, direction="reverse"))

    assert [c["label"] for c in calls] == [
        "pxrd_363K",
        "pxrd_343K",
        "pxrd_323K",
        "pxrd_303K",
    ]
    assert [c["anchor_volume"] for c in calls] == [None, 1012.0, 1006.0, 1002.0]
    assert all(c["anchor_max_jump"] == 0.03 for c in calls)
    assert result["chain_cell_direction"] == "reverse"
    assert [Path(r["file"]).stem for r in result["results"]] == [
        "pxrd_303K",
        "pxrd_323K",
        "pxrd_343K",
        "pxrd_363K",
    ]
