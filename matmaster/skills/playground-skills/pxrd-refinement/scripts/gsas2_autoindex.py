#!/usr/bin/env python3
"""
gsas2_autoindex.py — Ab-initio cell indexing for PXRD via GSAS-II DoIndexPeaks
(Visser algorithm). Returns candidate unit cells sorted by M20 figure of merit.

WHEN TO USE THIS SCRIPT

  Only when NO reference cell is available from any of:
    1. User / prompt / context
    2. Structural CIF / existing Rietveld
    3. Previous refinement on the same sample
    4. Literature values (search databases, ICSD, PDF2)

  A reference cell is ALWAYS preferred — ab-initio indexing can be slow
  (2–10 min for monoclinic) and brittle. Only fall back to this script
  when the caller has confirmed "no prior cell available".

WHY BASELINE-AWARE PEAK PICKING MATTERS

  DFT-simulated PXRD often has a high intensity offset (baseline ≈ peak top),
  so a naive `scipy.find_peaks(I/I_max, prominence=0.01)` silently drops
  low-angle peaks whose prominence is small relative to the baseline.
  Missing those low-angle reflections then caps the maximum d-spacing the
  indexer can see, producing wrong (sub- or super-cells).

  This script removes a rolling-percentile baseline BEFORE peak picking,
  which exposes the real reflections in both synthetic and experimental data.

GSAS-II path: /root/g2full/GSAS-II/GSASII  (override with --gsas2-path)

Usage:
  # Monoclinic-P search on a single pattern, 180s per Bravais family:
  python gsas2_autoindex.py \\
      --data pattern.xye \\
      --bravais monoclinic-P \\
      --wavelength 1.5406 \\
      --timeout 180 \\
      -o index_candidates.json

  # Multiple Bravais families (order matters for timeout budget):
  python gsas2_autoindex.py --data pattern.xye \\
      --bravais orthorhombic-P,monoclinic-P,triclinic --timeout 180

  # Wide-table CSV with a single temperature column:
  python gsas2_autoindex.py --data multi_temp.txt --wide-csv --column "140 C" \\
      --bravais monoclinic-P -o result.json

Output JSON:
  {
    "success": true,
    "n_peaks_picked": 30,
    "peaks_2theta": [...],
    "candidates": [
      {"M20": 28.4, "X20": 1, "bravais": "Monoclinic-P",
       "a": 10.83, "b": 10.21, "c": 9.20,
       "alpha": 90.0, "beta": 99.0, "gamma": 90.0,
       "volume": 1005.3},
      ...
    ],
    "best": {... same fields as candidates[0] ...},
    "preprocess": {"method": "rolling_percentile", "window_frac": 0.05,
                   "percentile": 10, "dynamic_range": 1.15, ...}
  }

Exit code is 0 on success (even if no candidates found), 2 on fatal error.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

# Shared curation pipeline (lives next to this script)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curation import CurationResult, curate, write_diagnostic_plot  # noqa: E402

DEFAULT_GSAS2_PATH = "/root/g2full/GSAS-II/GSASII"

BRAVAIS_MAP = {
    "cubic-F": 0, "cubic-I": 1, "cubic-P": 2,
    "trigonal-R": 3, "hexagonal-P": 4, "trigonal-hexagonal-P": 4,
    "tetragonal-I": 5, "tetragonal-P": 6,
    "orthorhombic-F": 7, "orthorhombic-I": 8, "orthorhombic-A": 9,
    "orthorhombic-B": 10, "orthorhombic-C": 11, "orthorhombic-P": 12,
    "monoclinic-I": 13, "monoclinic-A": 14, "monoclinic-C": 15,
    "monoclinic-P": 16, "triclinic": 17,
}
BRAVAIS_NAMES = ["Cubic-F", "Cubic-I", "Cubic-P", "Trigonal-R",
                 "Trigonal/Hexagonal-P", "Tetragonal-I", "Tetragonal-P",
                 "Orthorhombic-F", "Orthorhombic-I", "Orthorhombic-A",
                 "Orthorhombic-B", "Orthorhombic-C", "Orthorhombic-P",
                 "Monoclinic-I", "Monoclinic-A", "Monoclinic-C",
                 "Monoclinic-P", "Triclinic"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def setup_gsas2(path: str) -> None:
    if path not in sys.path:
        sys.path.insert(0, path)


def read_xye_or_xy(filepath: str) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            parts = re.split(r"[,\t\s]+", line)
            try:
                vals = [float(p) for p in parts if p]
            except ValueError:
                continue
            if len(vals) >= 2:
                rows.append((vals[0], vals[1]))
    if not rows:
        raise ValueError(f"No numeric rows in {filepath}")
    arr = np.array(rows)
    return arr[:, 0], arr[:, 1]


def read_wide_csv_column(filepath: str, column: str) -> tuple[np.ndarray, np.ndarray]:
    """Wide CSV layout: Angle,<col1>,Angle,<col2>,... Pick one column."""
    with open(filepath) as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    target = column.strip()
    for i in range(0, len(header), 2):
        if header[i + 1].strip() == target:
            tth = [float(r[i]) for r in rows]
            ii = [float(r[i + 1]) for r in rows]
            return np.array(tth), np.array(ii)
    raise ValueError(
        f"Column {target!r} not found in wide CSV; available: {[h.strip() for h in header[1::2]]}"
    )


def preprocess_for_indexing(
    two_theta: np.ndarray, intensity: np.ndarray,
    tmin: float | None = None, tmax: float | None = None,
    baseline_method: str = "piecewise_linear",
) -> tuple[np.ndarray, np.ndarray, CurationResult]:
    """Run the shared curation pipeline and return (tth, I_subtracted, result).

    Uses the MOR-slope artifact detector when `tmin` is not supplied, then
    fits a (piecewise) 1st-order baseline — indexing is sensitive to spurious
    low-angle "peaks" from the smooth monotonic artifact, so the curator's
    `tmin_cut` is usually mandatory on DFT-simulated data.
    """
    cr = curate(
        two_theta, intensity,
        baseline_method=baseline_method,
        tmin_hint=tmin, tmax_hint=tmax,
    )
    return cr.tth, cr.intensity_subtracted, cr


def pick_peaks(
    two_theta: np.ndarray, I_sub: np.ndarray,
    prominence_rel: float = 0.01, min_sep_points: int = 3, top_n: int = 30,
) -> list[tuple[float, float]]:
    """Fallback peak picker if curation didn't return a list.

    Prefer `peaks_from_curation`: it uses the same SNR/prominence criteria as
    the curation verdict, so what you see in the plot equals what goes to
    DoIndexPeaks. This routine is kept for smoke-tests / edge cases.
    """
    y = np.clip(I_sub, 0, None)
    ymax = float(y.max()) if y.size else 0.0
    if ymax <= 0:
        return []
    y_norm = y / ymax
    idx, props = find_peaks(y_norm, prominence=prominence_rel, distance=min_sep_points)
    if len(idx) == 0:
        return []
    order = np.argsort(props["prominences"])[::-1][:top_n]
    idx = np.sort(idx[order])
    return [(float(two_theta[i]), float(y_norm[i])) for i in idx]


def peaks_from_curation(cr: CurationResult, top_n: int = 30) -> list[tuple[float, float]]:
    """Reuse curation's peak positions, normalise intensities to [0, 1].

    `cr.peak_positions` is produced by the same SNR>=3 / prominence>=2% I_max
    rule the verdict uses; feeding those directly to DoIndexPeaks keeps the
    plot/verdict/indexer peak sets in sync and avoids re-tuning a second
    prominence threshold. We look up intensities in `cr.intensity_subtracted`
    (clipped to ≥ 0) via nearest-index on `cr.tth` and keep the top-N by
    intensity (Visser cares less about ordering than count).
    """
    if not cr.peak_positions:
        return []
    y = np.clip(cr.intensity_subtracted, 0, None)
    ymax = float(y.max()) if y.size else 0.0
    if ymax <= 0:
        return []
    picked: list[tuple[float, float]] = []
    for p in cr.peak_positions:
        i = int(np.argmin(np.abs(cr.tth - p)))
        picked.append((float(cr.tth[i]), float(y[i] / ymax)))
    picked.sort(key=lambda x: -x[1])
    picked = picked[:top_n]
    picked.sort(key=lambda x: x[0])
    return picked


def build_gsas2_peak_list(peaks_2th, wavelength: float):
    """Format expected by GSAS-II IndexPeaks / DoIndexPeaks:
    [2theta, intensity, use=True, indexed=False, h=0, k=0, l=0, d_obs, d_calc=0.0]
    """
    rows = []
    for tth, intens in peaks_2th:
        d = wavelength / (2.0 * np.sin(np.deg2rad(tth / 2.0)))
        rows.append([float(tth), float(intens), True, False, 0, 0, 0, float(d), 0.0])
    return rows


class _IndexTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _IndexTimeout()


def run_index(
    peak_list: list,
    bravais_idxs: list[int],
    wavelength: float,
    timeout_per_family: int,
    nc_ratio: int = 6,
    m20_min: float = 2.0,
    v1_hint: int = 0,
):
    """Call GSAS-II DoIndexPeaks for the selected Bravais families.

    Wraps the call in a SIGALRM so the wall-clock budget is actually
    enforced — GSAS-II's own ``timeout=`` kwarg is advisory and the
    monoclinic/triclinic Visser searches can run for hours past it.
    """
    import signal

    import GSASIIindex as G2idx  # noqa: E402

    bravais = [0] * 18
    for bi in bravais_idxs:
        bravais[bi] = 1
    controls = [float(wavelength), 0.0, int(nc_ratio), int(v1_hint)]

    t0 = time.time()
    log(
        f"DoIndexPeaks: families={[BRAVAIS_NAMES[i] for i in bravais_idxs]}, "
        f"timeout/family={timeout_per_family}s (hard, via SIGALRM), "
        f"Nc/No={nc_ratio}, M20_min={m20_min}"
    )
    cells: list = []
    prev_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(int(timeout_per_family * max(1, len(bravais_idxs))))
    try:
        result = G2idx.DoIndexPeaks(
            peak_list, controls, bravais, dlg=None,
            ifX20=True, timeout=timeout_per_family, M20_min=m20_min,
        )
        cells = result[-1] if isinstance(result, tuple) and result else result or []
    except _IndexTimeout:
        log(f"DoIndexPeaks hit hard SIGALRM timeout after "
            f"{int(time.time() - t0)}s — returning whatever was collected.")
    except Exception as e:
        import traceback
        log(f"DoIndexPeaks raised: {type(e).__name__}: {e}")
        log(traceback.format_exc())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)

    return cells, time.time() - t0


def format_candidates(cells) -> list[dict]:
    """DoIndexPeaks cells layout: [M20, X20, ibrav, a, b, c, alpha, beta, gamma, V, ...]"""
    out = []
    for c in cells:
        try:
            ibrav = int(c[2])
            name = BRAVAIS_NAMES[ibrav] if 0 <= ibrav < len(BRAVAIS_NAMES) else str(ibrav)
            out.append({
                "M20": round(float(c[0]), 3),
                "X20": int(c[1]),
                "bravais": name,
                "a": round(float(c[3]), 4),
                "b": round(float(c[4]), 4),
                "c": round(float(c[5]), 4),
                "alpha": round(float(c[6]), 3),
                "beta": round(float(c[7]), 3),
                "gamma": round(float(c[8]), 3),
                "volume": round(float(c[9]), 2),
            })
        except (IndexError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: -x["M20"])
    return out


def main():
    p = argparse.ArgumentParser(
        description="GSAS-II ab-initio indexing (Visser) with robust peak picking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--data", required=True, help="Input PXRD file (.xye / .xy / CSV)")
    p.add_argument("--wide-csv", action="store_true",
                   help="Input is a wide CSV (Angle,T1,Angle,T2,...); use with --column")
    p.add_argument("--column", help="Column label for --wide-csv mode (e.g. '140 C')")
    p.add_argument("--bravais", default="monoclinic-P",
                   help="Comma-separated Bravais families to try, in order. "
                        f"Choices: {','.join(BRAVAIS_MAP)}")
    p.add_argument("--wavelength", type=float, default=1.5406, help="X-ray wavelength (Å)")
    p.add_argument("--tmin", type=float, default=None, help="Minimum 2θ (°) to keep")
    p.add_argument("--tmax", type=float, default=None, help="Maximum 2θ (°) to keep")
    p.add_argument("--top-n-peaks", type=int, default=18,
                   help="Number of strongest peaks fed to the Visser indexer. "
                        "Calibrated to the Materials Studio Reflex-TREOR workflow "
                        "(typically 12–17 peaks of a manually curated list of "
                        "background-removed/smoothed data). Going much higher "
                        "(>25) starts feeding overlapping high-angle peaks that "
                        "Visser cannot single-index — it produces low-FOM "
                        "garbage candidates instead of a clean solution.")
    p.add_argument("--prominence-rel", type=float, default=0.01,
                   help="Relative prominence threshold (baseline-subtracted, 0–1)")
    p.add_argument("--tmax-index", type=float, default=35.0,
                   help="Maximum 2θ (°) used for indexing only. Material Studio "
                        "Reflex defaults to ≤35° because at higher angles peaks "
                        "overlap and Visser can't deterministically index them. "
                        "Set 0 to use --tmax (= raw data max).")
    p.add_argument("--timeout", type=int, default=180,
                   help="Seconds per Bravais family before giving up")
    p.add_argument("--nc-ratio", type=int, default=6, help="Max Nc/Nobs ratio (GSAS-II controls[2])")
    p.add_argument("--m20-min", type=float, default=2.0, help="Minimum M20 FOM to accept a cell")
    p.add_argument("--v1-hint", type=int, default=0,
                   help="Starting unit-cell volume guess in Å³ (0 = auto-sweep).")
    p.add_argument("--baseline-method",
                   choices=["piecewise_linear", "linear", "mor", "none"],
                   default="piecewise_linear",
                   help="Baseline model used by the curation pipeline. See "
                        "curation.py; default is piecewise-linear (three 1st-order "
                        "fits).")
    p.add_argument("--strict-curation", action="store_true",
                   help="Abort indexing when curation verdict is FAIL. Default: "
                        "still attempt indexing (FAIL is logged).")
    p.add_argument("--debug-plot", default=None,
                   help="If set, write <stem>_curation.png into this dir.")
    p.add_argument("-o", "--output", help="Write full JSON result here (also printed to stdout)")
    p.add_argument("--gsas2-path", default=DEFAULT_GSAS2_PATH)
    args = p.parse_args()

    setup_gsas2(args.gsas2_path)

    # Redirect GSAS-II stdout noise to stderr
    _orig_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        if args.wide_csv:
            if not args.column:
                raise SystemExit("--wide-csv requires --column")
            tth, I = read_wide_csv_column(args.data, args.column)
        else:
            tth, I = read_xye_or_xy(args.data)

        log(f"Loaded {len(tth)} points from {args.data}, 2θ {tth.min():.2f}–{tth.max():.2f}")

        raw_tth, raw_I = tth.copy(), I.copy()
        tth, I_sub, cr = preprocess_for_indexing(
            tth, I, args.tmin, args.tmax,
            baseline_method=args.baseline_method,
        )
        log(f"Curation: verdict={cr.verdict} tmin_cut={cr.tmin_cut:.3f} "
            f"dyn={cr.dyn_range:.1f} peaks={cr.peak_count} reasons={cr.reasons}")
        if cr.verdict == "FAIL" and args.strict_curation:
            raise SystemExit(
                f"Curation FAIL: {cr.reasons}. Pass --no-strict-curation to "
                "attempt indexing anyway."
            )
        if args.debug_plot:
            os.makedirs(args.debug_plot, exist_ok=True)
            png = os.path.join(args.debug_plot, Path(args.data).stem + "_curation.png")
            try:
                write_diagnostic_plot(cr, raw_tth, raw_I, png,
                                      title=Path(args.data).stem)
                log(f"Wrote curation plot → {png}")
            except Exception as exc:
                log(f"curation plot failed: {exc}")

        peaks = peaks_from_curation(cr, top_n=args.top_n_peaks)
        if len(peaks) < 6:
            log(f"Curation gave only {len(peaks)} peaks; falling back to "
                f"prominence-{args.prominence_rel} find_peaks on I_sub.")
            peaks = pick_peaks(
                tth, I_sub,
                prominence_rel=args.prominence_rel,
                top_n=args.top_n_peaks,
            )
        if args.tmax_index > 0:
            kept = [(t, h) for (t, h) in peaks if t <= args.tmax_index]
            if len(kept) >= 6:
                if len(kept) < len(peaks):
                    log(f"Trimming peaks to 2θ ≤ {args.tmax_index:g}° for "
                        f"indexing: {len(peaks)} → {len(kept)} (Material Studio "
                        f"Reflex default; high-angle overlaps confuse Visser).")
                peaks = kept
            else:
                log(f"WARN: --tmax-index={args.tmax_index} would leave only "
                    f"{len(kept)} peaks; keeping the full set ({len(peaks)}).")
        if len(peaks) < 6:
            raise SystemExit(
                f"Only {len(peaks)} peaks picked; need ≥ 6 for Visser indexing. "
                "Try lowering --prominence-rel or widening --tmin/--tmax."
            )
        log(f"Picked {len(peaks)} peaks for indexing (from curation)")
        for tth_, h in peaks:
            d = args.wavelength / (2 * np.sin(np.deg2rad(tth_ / 2)))
            log(f"  2θ={tth_:7.4f}°  d={d:7.4f} Å  I_norm={h:.3f}")

        fams = [f.strip() for f in args.bravais.split(",") if f.strip()]
        unknown = [f for f in fams if f not in BRAVAIS_MAP]
        if unknown:
            raise SystemExit(
                f"Unknown bravais family: {unknown}; must be one of {list(BRAVAIS_MAP)}"
            )
        bravais_idxs = [BRAVAIS_MAP[f] for f in fams]

        peak_list = build_gsas2_peak_list(peaks, args.wavelength)
        cells, dt = run_index(
            peak_list, bravais_idxs, args.wavelength,
            timeout_per_family=args.timeout,
            nc_ratio=args.nc_ratio, m20_min=args.m20_min, v1_hint=args.v1_hint,
        )
        log(f"DoIndexPeaks finished in {dt:.1f}s, got {len(cells)} candidates")

        candidates = format_candidates(cells)
        result = {
            "success": True,
            "n_peaks_picked": len(peaks),
            "peaks_2theta": [round(p_[0], 4) for p_ in peaks],
            "candidates": candidates,
            "best": candidates[0] if candidates else None,
            "curation": cr.summary_dict(),
            "search": {
                "bravais_families": fams,
                "timeout_per_family_s": args.timeout,
                "nc_ratio": args.nc_ratio,
                "m20_min": args.m20_min,
                "v1_hint": args.v1_hint,
                "elapsed_s": round(dt, 2),
            },
        }

        payload = json.dumps(result, indent=2)
        sys.stdout = _orig_stdout
        print(payload)
        if args.output:
            with open(args.output, "w") as f:
                f.write(payload)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        sys.stdout = _orig_stdout
        err = {"success": False, "error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()}
        print(json.dumps(err, indent=2))
        sys.exit(2)


if __name__ == "__main__":
    main()
