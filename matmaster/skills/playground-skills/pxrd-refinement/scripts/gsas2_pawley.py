#!/usr/bin/env python3
"""
gsas2_pawley.py — GSAS-II Pawley refinement for PXRD data.

Refines lattice parameters from powder XRD data using GSAS-II full-pattern
Pawley extraction. Outputs cell parameters, ESDs, and R-factors as JSON.

GSAS-II path: /root/g2full/GSAS-II/GSASII  (override with --gsas2-path)

Usage (example values are Si / cubic; replace `--space-group` and `--cell` with the
user-provided initial cell — do NOT invent one from peak positions):
  # Single pattern:
  python gsas2_pawley.py \\
    --data pattern.xye \\
    --space-group "F d -3 m" \\
    --cell "a=5.43,b=5.43,c=5.43" \\
    --wavelength 1.5406 \\
    -o result.json

  # Directory of patterns (e.g. multi-temperature):
  python gsas2_pawley.py \\
    --data /path/to/patterns/ \\
    --space-group "<SG>" \\
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \\
    --wavelength 1.5406 \\
    -o results.json

  # Wide-table CSV (multiple temperatures in one file):
  python gsas2_pawley.py \\
    --data multi_temp.txt \\
    --wide-csv \\
    --space-group "<SG>" \\
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" \\
    -o results.json

Output JSON (single pattern, illustrative Si values):
  {
    "success": true, "file": "pattern.xye",
    "a": 5.431, "b": 5.431, "c": 5.431,
    "alpha": 90.0, "beta": 90.0, "gamma": 90.0,
    "volume": 160.19,
    "a_esd": 0.0002, ...
    "wR": 8.5, "n_reflections": 12
  }

Output JSON (multi-pattern):
  {"success": true, "results": [...per-pattern dicts...]}
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

# Local imports: curation + pawley_core live next to this script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curation import CurationResult, curate, write_diagnostic_plot  # noqa: E402
from pawley_core import (  # noqa: E402
    perturb_seed_cells,
    run_pawley_once,
    summarize_multi_start,
)

DEFAULT_GSAS2_PATH = "/root/g2full/GSAS-II/GSASII"

# Default Cu Kα1 instrument parameter file content
DEFAULT_INSTPRM = """\
#GSAS-II instrument parameter file; do not add/delete items!
Type: PXC
Bank: 1
Lam: 1.5406
Polariz.: 0.99
Azimuth: 0.0
Zero: 0.0
U: 2.0
V: -2.0
W: 5.0
X: 0.0
Y: 0.0
Z: 0.0
SH/L: 0.002
"""

# Monoclinic parameters: which cell params exist for each crystal system
CELL_PARAMS = {
    "cubic": ["a"],
    "tetragonal": ["a", "c"],
    "hexagonal": ["a", "c"],
    "trigonal": ["a", "c"],
    "orthorhombic": ["a", "b", "c"],
    "monoclinic": ["a", "b", "c", "beta"],
    "triclinic": ["a", "b", "c", "alpha", "beta", "gamma"],
}


def setup_gsas2(gsas2_path: str) -> None:
    """Add GSAS-II to sys.path."""
    if gsas2_path not in sys.path:
        sys.path.insert(0, gsas2_path)


def make_instprm_file(wavelength: float, tmpdir: str) -> str:
    """Write a temporary instrument parameter file and return its path."""
    content = DEFAULT_INSTPRM.replace("Lam: 1.5406", f"Lam: {wavelength:.6f}")
    path = os.path.join(tmpdir, "instrument.instprm")
    with open(path, "w") as f:
        f.write(content)
    return path


def preprocess_to_xye(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    outpath: str,
    warnings: list[str] | None = None,
) -> dict:
    """
    Adaptive preprocessing of raw intensity data, written as GSAS-II .xye.

    Decision is driven by the data's dynamic range (max/p5):
      • dynamic range < 10  → DFT-style flat data (baseline ≈ peak top).
        Subtract 5th-percentile baseline and scale by 1e4 to give GSAS-II
        sensible count values (otherwise sigma=√I is meaningless).
      • dynamic range ≥ 10  → real experimental counts. Pass through
        unchanged (only ensure >=1 for sqrt). Touching it would distort
        the proper Poisson sigma weights.

    Returns a dict describing what was done (mode, scale, baseline) so the
    caller can log the decision for transparency.
    """
    if warnings is None:
        warnings = []

    p5 = float(np.percentile(intensity, 5))
    pmax = float(np.max(intensity))
    pmin = float(np.min(intensity))

    # Avoid divide-by-zero for the dynamic range estimate
    denom = max(p5, 1e-9)
    dyn_range = (pmax - pmin) / denom if denom > 0 else float("inf")

    if dyn_range < 10.0:
        # Synthetic / DFT-style flat data: scale up so sigma=√I has meaning
        baseline = p5
        scale = 1e4
        y = (intensity - baseline) * scale
        mode = "dft_scaled"
        info = {"baseline": baseline, "scale": scale}
    else:
        # Real experimental counts: pass through unchanged
        y = intensity.astype(float)
        mode = "passthrough"
        info = {"baseline": 0.0, "scale": 1.0}

    y = np.maximum(y, 1.0)
    sigma = np.sqrt(y)
    np.savetxt(outpath, np.column_stack([two_theta, y, sigma]), fmt="%.7f")

    if mode == "dft_scaled":
        warnings.append(
            f"preprocess: low dynamic range ({dyn_range:.2f}) detected, "
            f"applied baseline subtraction (-{p5:.4f}) and scale ×{int(info['scale'])}"
        )

    return {"mode": mode, "dynamic_range": round(dyn_range, 3), **info}


def read_xy_file(filepath: str) -> tuple[np.ndarray, np.ndarray]:
    """Read a simple two-column (2theta, intensity) text file."""
    data = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            parts = re.split(r"[,\t\s]+", line)
            try:
                vals = [float(p) for p in parts if p]
                if len(vals) >= 2:
                    data.append((vals[0], vals[1]))
            except ValueError:
                continue
    if not data:
        raise ValueError(f"No numeric data in {filepath}")
    arr = np.array(data)
    return arr[:, 0], arr[:, 1]


def parse_wide_csv(
    filepath: str,
) -> list[dict]:
    """
    Parse a wide-table CSV with paired (angle, intensity) columns per temperature.

    Header format:  Angle,140 C,Angle,130 C,...
    Returns list of dicts: {temp_label, temp_c, two_theta, intensity}
    """
    with open(filepath) as f:
        reader = csv.reader(f)
        header = next(reader)

    # Extract temperature columns
    temp_cols = []
    for i in range(0, len(header), 2):
        if i + 1 >= len(header):
            break
        label = header[i + 1].strip()
        m = re.search(r"(\d+)", label)
        if m:
            temp_c = int(m.group(1))
            temp_cols.append((i, i + 1, temp_c, label))

    # Read data
    rows = []
    with open(filepath) as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row:
                try:
                    rows.append([float(v) if v.strip() else 0.0 for v in row])
                except ValueError:
                    continue

    patterns = []
    for angle_col, int_col, temp_c, label in temp_cols:
        two_theta = np.array([r[angle_col] for r in rows])
        intensity = np.array([r[int_col] for r in rows])
        patterns.append(
            {
                "temp_label": label,
                "temp_c": temp_c,
                "two_theta": two_theta,
                "intensity": intensity,
            }
        )
    return patterns


def parse_cell_string(cell_str: str) -> dict:
    """Parse cell string like 'a=5.43,b=5.43,c=5.43' or 'a=10.0,b=9.5,c=8.2,beta=99.0'."""
    parts = {}
    for item in cell_str.split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k.strip().lower()] = float(v.strip())
    return parts


def cell_dict_to_list(cell_dict: dict) -> list[float]:
    """Convert cell dict to GSAS-II cell list [a,b,c,alpha,beta,gamma]."""
    return [
        cell_dict.get("a", 5.0),
        cell_dict.get("b", cell_dict.get("a", 5.0)),
        cell_dict.get("c", 5.0),
        cell_dict.get("alpha", 90.0),
        cell_dict.get("beta", 90.0),
        cell_dict.get("gamma", 90.0),
    ]


def refine_one_pattern(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    space_group: str,
    cell_list: list[float],
    wavelength: float,
    dmin: float,
    two_theta_min: float | None,
    two_theta_max: float | None,
    instprm_path: str,
    workdir: str,
    label: str = "pattern",
    dmax: float | None = None,
    debug_plot: str | None = None,
    curation_mode: str = "auto",
    baseline_method: str = "piecewise_linear",
    multi_start: int = 1,
    multi_start_seed: int = 42,
    multi_start_len_sigma: float = 0.005,
    multi_start_ang_sigma: float = 0.5,
) -> dict:
    """
    Run GSAS-II Pawley refinement on a single pattern.

    ``curation_mode``
      - ``off``: skip data curation entirely; use user-supplied tmin/tmax.
      - ``auto``: run curation; use its tmin_cut when the user did not
        pass ``two_theta_min``. Surface verdict in warnings; FAIL records
        a warning but still refines (so the caller can see the numbers).
      - ``strict``: run curation; abort refinement on a FAIL verdict.
    ``baseline_method``
      Forwarded to ``curation.curate`` when curation is enabled.
    ``multi_start``
      Number of independent Pawley runs to launch from perturbed initial
      cells, keeping the run with the lowest wR. ``1`` (default) preserves
      the legacy single-shot behavior; ``5`` is the recommended setting
      for noisy / DFT-simulated data and any pattern that gave wR > 20%
      with a single shot. Perturbations are deterministic given
      ``multi_start_seed``.
    ``multi_start_len_sigma`` / ``multi_start_ang_sigma``
      Std-dev of multiplicative log-normal perturbations on a/b/c (default
      0.5 %) and additive perturbations on α/β/γ (default 0.5°). Tighten
      to stay near the user-provided cell; loosen to widen the search.

    Returns a dict with cell parameters (best run), ESDs, R-factors, plus
    a ``warnings`` list and (when ``multi_start > 1``) a ``multi_start``
    summary listing every candidate's seed cell + wR + outcome.
    """
    warnings: list[str] = []

    curation: CurationResult | None = None
    if curation_mode != "off":
        try:
            curation = curate(
                two_theta,
                intensity,
                baseline_method=baseline_method,
                tmin_hint=two_theta_min,
                tmax_hint=two_theta_max,
            )
            warnings.append(
                f"curation verdict={curation.verdict} "
                f"tmin_cut={curation.tmin_cut:.3f} dyn={curation.dyn_range:.1f} "
                f"peaks={curation.peak_count} "
                f"reasons={curation.reasons}"
            )
            if curation.verdict == "FAIL" and curation_mode == "strict":
                return {
                    "success": False,
                    "file": label,
                    "error": f"curation FAIL: {curation.reasons}",
                    "curation": curation.summary_dict(),
                    "warnings": warnings,
                }
            if two_theta_min is None and curation.tmin_cut > float(two_theta.min()):
                two_theta_min = curation.tmin_cut
                warnings.append(
                    f"auto-applied tmin={curation.tmin_cut:.3f} from curation"
                )
        except Exception as exc:
            warnings.append(f"curation skipped: {type(exc).__name__}: {exc}")

    xye_path = os.path.join(workdir, f"{label}.xye")
    preprocess_info = preprocess_to_xye(two_theta, intensity, xye_path, warnings)

    # If user did not specify limits, fall back to the full data range.
    # Clamp to the actual data range so a stray --tmin/--tmax can't produce an
    # empty / inverted window that GSAS-II will silently "refine" against.
    data_lo = float(two_theta.min())
    data_hi = float(two_theta.max())
    lim_lo = float(two_theta_min) if two_theta_min is not None else data_lo
    lim_hi = float(two_theta_max) if two_theta_max is not None else data_hi
    lim_lo = max(data_lo, min(lim_lo, data_hi))
    lim_hi = max(data_lo, min(lim_hi, data_hi))
    if lim_lo >= lim_hi:
        warnings.append(
            f"invalid 2θ limits [{lim_lo:.4f}, {lim_hi:.4f}] vs data "
            f"[{data_lo:.4f}, {data_hi:.4f}]; falling back to full range"
        )
        lim_lo, lim_hi = data_lo, data_hi

    k = max(1, int(multi_start))
    seeds = perturb_seed_cells(
        cell_list, k, multi_start_len_sigma, multi_start_ang_sigma, multi_start_seed
    )

    candidates: list[dict] = []
    for i, seed in enumerate(seeds):
        sub_label = label if k == 1 else f"{label}__ms{i}"
        only_debug_plot = debug_plot if i == 0 else None
        cand = run_pawley_once(
            xye_path=xye_path,
            instprm_path=instprm_path,
            space_group=space_group,
            cell_list=seed,
            dmin=dmin,
            dmax=dmax,
            lim_lo=lim_lo,
            lim_hi=lim_hi,
            workdir=workdir,
            label=sub_label,
            debug_plot=only_debug_plot,
        )
        cand["_seed_index"] = i
        cand["_seed_cell"] = [round(float(v), 5) for v in seed]
        candidates.append(cand)

    def _wr_key(c: dict) -> float:
        if not c.get("success"):
            return float("inf")
        wr = c.get("wR")
        return float(wr) if wr is not None else float("inf")

    best = min(candidates, key=_wr_key)
    if not best.get("success"):
        return {
            "success": False,
            "file": label,
            "error": best.get("error", "all multi-start runs failed"),
            "preprocess": preprocess_info,
            "limits": [round(lim_lo, 4), round(lim_hi, 4)],
            "curation": curation.summary_dict() if curation is not None else None,
            "multi_start": summarize_multi_start(candidates) if k > 1 else None,
            "warnings": warnings + best.get("warnings", []),
        }

    best_warnings = best.get("warnings", [])

    result = {
        "success": True,
        "file": label,
        "a": best["a"],
        "b": best["b"],
        "c": best["c"],
        "alpha": best["alpha"],
        "beta": best["beta"],
        "gamma": best["gamma"],
        "volume": best["volume"],
        "wR": best.get("wR"),
        "n_reflections": best.get("n_reflections"),
        "limits": [round(lim_lo, 4), round(lim_hi, 4)],
        "preprocess": preprocess_info,
        "warnings": warnings + best_warnings,
    }
    for k_esd in ("a_esd", "b_esd", "c_esd", "alpha_esd", "beta_esd", "gamma_esd"):
        if k_esd in best:
            result[k_esd] = best[k_esd]
    if curation is not None:
        result["curation"] = curation.summary_dict()
    if k > 1:
        result["multi_start"] = summarize_multi_start(candidates)
        # surface a brief audit line so users grep'ing logs see the win
        chosen = best["_seed_index"]
        wRs = [(c.get("wR") if c.get("success") else None) for c in candidates]
        result["warnings"].append(
            f"multi-start picked seed {chosen}/{k - 1} (wR list={wRs})"
        )

    if debug_plot and curation is not None:
        try:
            png = os.path.join(debug_plot, f"{label}_curation.png")
            write_diagnostic_plot(curation, two_theta, intensity, png, title=label)
        except Exception as exc:
            result["warnings"].append(f"curation plot failed: {exc}")

    return result


def _refine_kwargs_from_args(args) -> dict:
    """Common keyword args shared by run_single / run_directory / run_wide_csv."""
    return {
        "wavelength": args.wavelength,
        "dmin": args.dmin,
        "dmax": args.dmax,
        "two_theta_min": args.tmin,
        "two_theta_max": args.tmax,
        "debug_plot": args.debug_plot,
        "curation_mode": args.curation_mode,
        "baseline_method": args.baseline_method,
        "multi_start": args.multi_start,
        "multi_start_seed": args.multi_start_seed,
        "multi_start_len_sigma": args.multi_start_len_sigma,
        "multi_start_ang_sigma": args.multi_start_ang_sigma,
    }


def _accept_chain_promotion(
    prev: dict | None,
    curr: dict,
    wr_max: float,
    vol_jump_max: float,
) -> tuple[bool, str]:
    """
    Quality gate for cell promotion across a chained temperature series.

    Promote curr → next iff curr converged with a tolerable wR AND its
    volume is within ``vol_jump_max`` of the previously accepted result
    (or no prior accepted result exists). Returns (accept, reason).
    """
    if not curr.get("success"):
        return False, "current refinement failed"
    wr = curr.get("wR")
    if wr is None or wr > wr_max:
        return False, f"wR={wr} exceeds gate {wr_max}"
    if prev is not None and prev.get("success"):
        v_prev = prev.get("volume")
        v_curr = curr.get("volume")
        if v_prev and v_curr:
            jump = abs(v_curr - v_prev) / v_prev
            if jump > vol_jump_max:
                return False, f"ΔV/V={jump:.3f} exceeds gate {vol_jump_max}"
    return True, "ok"


def run_single(args) -> dict:
    """Refine a single PXRD file."""
    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)

    two_theta, intensity = read_xy_file(args.data)

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        result = refine_one_pattern(
            two_theta=two_theta,
            intensity=intensity,
            space_group=args.space_group,
            cell_list=cell_list,
            instprm_path=instprm,
            workdir=tmpdir,
            label=Path(args.data).stem,
            **_refine_kwargs_from_args(args),
        )
    result["file"] = args.data
    return result


def _maybe_promote_cell(
    args,
    last_accepted: dict | None,
    curr: dict,
) -> tuple[list[float] | None, dict | None, str]:
    """
    Decide whether ``curr`` should be promoted to seed the next pattern in a
    chained refinement series. Returns (new_cell_or_None, new_last_accepted,
    reason). ``new_cell_or_None`` is ``None`` when promotion is rejected,
    in which case the caller keeps using whatever cell it was using.
    """
    if not args.chain_cell:
        return None, last_accepted, "chain disabled"
    accept, reason = _accept_chain_promotion(
        last_accepted, curr, args.chain_wr_max, args.chain_vol_jump_max
    )
    if not accept:
        return None, last_accepted, reason
    next_cell = [
        curr["a"],
        curr["b"],
        curr["c"],
        curr["alpha"],
        curr["beta"],
        curr["gamma"],
    ]
    return next_cell, curr, reason


def run_directory(args) -> dict:
    """Refine all PXRD files in a directory."""
    data_dir = Path(args.data)
    exts = ("*.xye", "*.xy", "*.dat", "*.csv", "*.txt", "*.raw")
    files = []
    for ext in exts:
        files.extend(data_dir.glob(ext))
    files = sorted(set(files))

    if not files:
        return {"success": False, "error": f"No data files in {args.data}"}

    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    results = []

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for fpath in files:
            try:
                two_theta, intensity = read_xy_file(str(fpath))
                r = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=fpath.stem,
                    **_refine_kwargs_from_args(args),
                )
                r["file"] = str(fpath)
                r["seed_cell"] = [round(float(v), 5) for v in current_cell]
                next_cell, last_accepted, reason = _maybe_promote_cell(
                    args, last_accepted, r
                )
                r["chain_decision"] = (
                    "promoted" if next_cell is not None else f"rejected: {reason}"
                )
                if next_cell is not None:
                    current_cell = next_cell
            except Exception as exc:
                r = {"success": False, "file": str(fpath), "error": str(exc)}
            results.append(r)

    return {"success": True, "results": results}


def run_wide_csv(args) -> dict:
    """Parse wide-table CSV (multiple temperatures), refine each column."""
    patterns = parse_wide_csv(args.data)
    if not patterns:
        return {"success": False, "error": "No temperature columns found in wide CSV"}

    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    results = []

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for pat in patterns:
            label = f"T{pat['temp_c']}C"
            try:
                r = refine_one_pattern(
                    two_theta=pat["two_theta"],
                    intensity=pat["intensity"],
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=label,
                    **_refine_kwargs_from_args(args),
                )
                r["temp_c"] = pat["temp_c"]
                r["temp_label"] = pat["temp_label"]
                r["seed_cell"] = [round(float(v), 5) for v in current_cell]
                next_cell, last_accepted, reason = _maybe_promote_cell(
                    args, last_accepted, r
                )
                r["chain_decision"] = (
                    "promoted" if next_cell is not None else f"rejected: {reason}"
                )
                if next_cell is not None:
                    current_cell = next_cell
            except Exception as exc:
                r = {
                    "success": False,
                    "temp_c": pat["temp_c"],
                    "temp_label": pat["temp_label"],
                    "error": str(exc),
                }
            results.append(r)

    return {"success": True, "results": results}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GSAS-II Pawley refinement for PXRD data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--data",
        required=True,
        help="PXRD data file (.xye/.xy/.dat/.csv), directory of patterns, "
        "or wide-table CSV (use --wide-csv)",
    )
    ap.add_argument(
        "--space-group",
        required=True,
        help='GSAS-II space group string, e.g. "P 21/c" or "P n m a"',
    )
    ap.add_argument(
        "--cell",
        required=True,
        help='Initial lattice params (cubic: "a=5.43,b=5.43,c=5.43"; monoclinic: "a=...,b=...,c=...,beta=...")',
    )
    ap.add_argument(
        "--wavelength",
        type=float,
        default=1.5406,
        help="X-ray wavelength in Å (default: Cu Kα1 = 1.5406)",
    )
    ap.add_argument(
        "--dmin",
        type=float,
        default=2.0,
        help="Minimum d-spacing for Pawley reflections in Å (default: 2.0). "
        "For high-resolution / large 2θ range data, lower (e.g. 1.0); "
        "for noisy low-resolution data, raise (e.g. 2.5).",
    )
    ap.add_argument(
        "--dmax",
        type=float,
        default=None,
        help="Maximum d-spacing for Pawley reflections in Å "
        "(default: None = no upper cap). Set when first reflection is far "
        "below tmin and you want to skip it.",
    )
    ap.add_argument(
        "--tmin",
        type=float,
        default=None,
        help="Lower 2θ limit for refinement (default: None = full data range)",
    )
    ap.add_argument(
        "--tmax",
        type=float,
        default=None,
        help="Upper 2θ limit for refinement (default: None = full data range)",
    )
    ap.add_argument(
        "--instprm",
        default=None,
        help="Path to GSAS-II instrument parameter file. Default auto-generates "
        "a Cu Kα template with conservative U/V/W tuned for synchrotron-style "
        "narrow peaks; for lab diffractometers you SHOULD provide your own "
        "instprm calibrated against a standard (e.g. LaB6/Si).",
    )
    ap.add_argument(
        "--gsas2-path",
        default=DEFAULT_GSAS2_PATH,
        help=f"Path to GSAS-II GSASII directory (default: {DEFAULT_GSAS2_PATH})",
    )
    ap.add_argument(
        "--wide-csv",
        action="store_true",
        help="Input is a wide-table CSV with multiple temperature columns "
        "(header: Angle, T1, Angle, T2, ...)",
    )
    ap.add_argument(
        "--chain-cell",
        action="store_true",
        help="In multi-pattern modes (directory / wide-csv), feed the refined "
        "cell of pattern N into pattern N+1 as starting point. Off by default "
        "because it propagates errors and may straddle a phase transition. "
        "Promotion is gated by --chain-wr-max and --chain-vol-jump-max so a "
        "bad refinement cannot poison the rest of the series.",
    )
    ap.add_argument(
        "--chain-wr-max",
        type=float,
        default=25.0,
        help="(With --chain-cell) Max wR (%%) for a refinement to be eligible "
        "to seed the next pattern. Above this, the chain falls back to the "
        "last accepted cell or the user-provided cell. Default: 25.",
    )
    ap.add_argument(
        "--chain-vol-jump-max",
        type=float,
        default=0.05,
        help="(With --chain-cell) Max relative volume change vs. the previously "
        "accepted refinement (default 0.05 = 5%%). Larger jumps suggest a "
        "phase transition or a bad local min — reject the promotion. Set to a "
        "large value (e.g. 1.0) to disable the volume gate.",
    )
    ap.add_argument(
        "--multi-start",
        type=int,
        default=1,
        help="Number of independent Pawley runs per pattern, each from a "
        "perturbed initial cell; the lowest-wR result is returned. Default 1 "
        "(legacy single-shot). Use 5 for noisy / DFT-simulated data or any "
        "pattern where a single shot gave wR > 20%%.",
    )
    ap.add_argument(
        "--multi-start-seed",
        type=int,
        default=42,
        help="RNG seed for multi-start perturbations. Identical seed → "
        "identical seed cells across runs (reproducible).",
    )
    ap.add_argument(
        "--multi-start-len-sigma",
        type=float,
        default=0.005,
        help="Std-dev of multiplicative log-normal perturbations on a/b/c "
        "for multi-start (default 0.005 = 0.5%%).",
    )
    ap.add_argument(
        "--multi-start-ang-sigma",
        type=float,
        default=0.5,
        help="Std-dev of additive perturbations (degrees) on α/β/γ for "
        "multi-start (default 0.5°).",
    )
    ap.add_argument(
        "--debug-plot",
        default=None,
        help="If set, write per-pattern <label>_pattern.csv (2θ, yobs, ycalc, "
        "diff) and, when curation runs, <label>_curation.png into this dir.",
    )
    ap.add_argument(
        "--curation-mode",
        choices=["off", "auto", "strict"],
        default="auto",
        help="Data curation behaviour. 'auto' (default): detect artifact "
        "prefix + assign PASS/WARN/FAIL; override tmin when user didn't set "
        "one, but still refine. 'strict': abort refinement on FAIL. 'off': "
        "use user-supplied tmin/tmax only, no curation.",
    )
    ap.add_argument(
        "--baseline-method",
        choices=["piecewise_linear", "linear", "mor", "none"],
        default="piecewise_linear",
        help="Baseline model used by curation (not by GSAS-II background). "
        "Prefer 'piecewise_linear' (three stitched 1st-order fits); fall back "
        "to 'linear' for very clean / near-stationary backgrounds, or 'mor' "
        "for highly curved baselines (accept bg_median bias).",
    )
    ap.add_argument("-o", "--output", help="Write JSON output to this file")
    args = ap.parse_args()

    setup_gsas2(args.gsas2_path)

    data_path = Path(args.data)

    # GSAS-II writes progress/SVD warnings to stdout via bare print(). Scope
    # the redirect so any uncaught exception inside the refinement still leaves
    # stdout in its original state, and so we never accidentally swallow a
    # caller's stdout context.
    if not (args.wide_csv or data_path.is_dir() or data_path.is_file()):
        print(
            json.dumps({"success": False, "error": f"Not found: {args.data}"}),
        )
        sys.exit(1)

    with redirect_stdout(sys.stderr):
        if args.wide_csv:
            result = run_wide_csv(args)
        elif data_path.is_dir():
            result = run_directory(args)
        else:
            result = run_single(args)

    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    print(json_str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
