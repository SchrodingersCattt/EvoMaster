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

# Local import: curation lives next to this script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curation import CurationResult, curate, write_diagnostic_plot  # noqa: E402

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


def generate_pawley_reflections(
    phase_data: dict, dmin: float, dmax: float | None = None
) -> list:
    """
    Generate and estimate Pawley reflection list.

    Mirrors GSAS-II's 'Pawley create' + 'Pawley estimate' GUI operations,
    which are not exposed in GSASIIscriptable directly.

    `dmax` caps the maximum d-spacing considered; pass None for no upper
    cap (use the full set of reflections >= dmin).
    """
    import GSASIIlattice as G2lat
    import GSASIImath as G2mth
    import GSASIIspc as G2spc

    generalData = phase_data["General"]
    cell = generalData["Cell"][1:7]
    A = G2lat.cell2A(cell)
    SGData = generalData["SGData"]
    if dmax is None:
        dmax = generalData.get("Pawley dmax", 100.0)

    HKLd = np.array(G2lat.GenHLaue(dmin, SGData, A))
    peaks = []
    for h, k, l, d in HKLd:
        if d > dmax:
            continue
        ext, mul = G2spc.GenHKLf([int(h), int(k), int(l)], SGData)[:2]
        if not ext:
            mul *= 2
            peaks.append([int(h), int(k), int(l), mul, d, True, 1.0, 1.0])
    peaks = G2mth.sortArray(peaks, 4, reverse=True)
    return peaks


def estimate_pawley_intensities(
    peaks: list,
    xdata: np.ndarray,
    yobs: np.ndarray,
    inst_parms: dict,
    sample_parms: dict,
    cell_volume: float,
) -> list:
    """
    Initialize Pawley reflection intensities from observed pattern.

    Mirrors GSAS-II's 'Pawley estimate' operation. Each reflection's F^2
    is estimated from the observed peak height at the reflection position.
    """
    import GSASIIlattice as G2lat
    import GSASIIpwd as G2pwd

    Vst = 1.0 / cell_volume

    for ref in peaks:
        d = ref[4]
        pos = G2lat.Dsp2pos(inst_parms, d)
        indx = np.searchsorted(xdata, pos)
        if 0 <= indx < len(yobs):
            try:
                fwhm = max(0.001, G2pwd.getFWHM(pos, inst_parms))
                ref[6] = max(yobs[indx], 1.0) * fwhm * np.sqrt(np.pi)
                # Lorentz-polarization correction for CW X-ray
                lp = 1.0 / (
                    2.0
                    * np.sin(np.radians(pos / 2.0)) ** 2
                    * np.cos(np.radians(pos / 2.0))
                )
                ref[6] /= sample_parms["Scale"][0] * Vst * lp * ref[3]
            except Exception:
                ref[6] = 1.0
        else:
            ref[6] = 1.0
    return peaks


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

    Returns a dict with cell parameters, ESDs, R-factors, and a ``warnings``
    list describing preprocessing / curation decisions and any per-step
    refinement failures (these don't crash the run but ARE surfaced).
    """
    import GSASIIscriptable as G2sc

    G2sc.SetPrintLevel("warn")
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

    gpx_path = os.path.join(workdir, f"{label}.gpx")
    gpx = G2sc.G2Project(newgpx=gpx_path)

    hist = gpx.add_powder_histogram(xye_path, iparams=instprm_path)
    phase = gpx.add_phase(
        phasename="phase",
        spacegroup=space_group,
        cell=cell_list,
        histograms=[hist],
    )

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
    hist.set_refinements({"Limits": [lim_lo, lim_hi]})

    phase.setPhaseEntryValue(["General", "doPawley"], True)
    phase.setPhaseEntryValue(["General", "Pawley dmin"], dmin)
    if dmax is not None:
        phase.setPhaseEntryValue(["General", "Pawley dmax"], dmax)

    peaks = generate_pawley_reflections(phase.data, dmin, dmax)
    xdata = hist.getdata("x")
    yobs = hist.getdata("yobs")
    inst_parms = hist.getHistEntryValue(["Instrument Parameters"])[0]
    sample_parms = hist.getHistEntryValue(["Sample Parameters"])
    cell_vol = phase.data["General"]["Cell"][7]

    peaks = estimate_pawley_intensities(
        peaks, xdata, yobs, inst_parms, sample_parms, cell_vol
    )
    phase.data["Pawley ref"] = peaks

    # Fix histogram scale — must not refine it simultaneously with Pawley
    # intensities (completely correlated → SVD singularity)
    hist.setHistEntryValue(["Sample Parameters", "Scale"], [1.0, False])

    gpx.set_Controls("cycles", 10)

    def _safe_refine(step_name: str) -> None:
        try:
            gpx.do_refinements([{}])
        except Exception as exc:
            msg = f"refine step '{step_name}' raised {type(exc).__name__}: {exc}"
            warnings.append(msg)
            print(f"[gsas2_pawley][{label}] WARN {msg}", file=sys.stderr)

    hist.set_refinements({"Background": {"no. coeffs": 6, "refine": True}})
    _safe_refine("Background")

    phase.set_refinements({"Cell": True})
    _safe_refine("Cell")

    hist.set_refinements({"Instrument Parameters": ["U", "V", "W"]})
    _safe_refine("UVW")

    hist.set_refinements({"Instrument Parameters": ["Zero"]})
    _safe_refine("Zero")

    hist.set_refinements({"Background": {"no. coeffs": 12, "refine": True}})
    for i in range(3):
        _safe_refine(f"converge_{i + 1}")

    cell = phase.get_cell()
    try:
        cell_esd = phase.get_cell_and_esd()
    except Exception as exc:
        cell_esd = None
        warnings.append(f"get_cell_and_esd failed: {exc}")

    wR = hist.get_wR()
    n_reflections = len(phase.data.get("Pawley ref", []))

    if wR is not None and wR > 30.0:
        warnings.append(
            f"high wR ({wR:.2f}%); refinement likely poor — check initial cell, "
            f"space group, peak-shape, or 2θ range"
        )

    result = {
        "success": True,
        "file": label,
        "a": round(cell["length_a"], 5),
        "b": round(cell["length_b"], 5),
        "c": round(cell["length_c"], 5),
        "alpha": round(cell["angle_alpha"], 4),
        "beta": round(cell["angle_beta"], 4),
        "gamma": round(cell["angle_gamma"], 4),
        "volume": round(cell["volume"], 4),
        "wR": round(wR, 2) if wR is not None else None,
        "n_reflections": n_reflections,
        "limits": [round(lim_lo, 4), round(lim_hi, 4)],
        "preprocess": preprocess_info,
        "warnings": warnings,
    }
    if curation is not None:
        result["curation"] = curation.summary_dict()

    if cell_esd is not None:
        try:
            esd_dict = cell_esd[1] if isinstance(cell_esd, (tuple, list)) else {}
            key_map = {
                "a": "length_a",
                "b": "length_b",
                "c": "length_c",
                "alpha": "angle_alpha",
                "beta": "angle_beta",
                "gamma": "angle_gamma",
            }
            for param, key in key_map.items():
                val = esd_dict.get(key, 0.0)
                result[f"{param}_esd"] = round(float(val or 0.0), 6)
        except Exception as exc:
            warnings.append(f"ESD extraction failed: {exc}")

    if debug_plot:
        try:
            _write_debug_plot(hist, debug_plot, label)
        except Exception as exc:
            warnings.append(f"debug plot failed: {exc}")
        if curation is not None:
            try:
                png = os.path.join(debug_plot, f"{label}_curation.png")
                write_diagnostic_plot(curation, two_theta, intensity, png, title=label)
            except Exception as exc:
                warnings.append(f"curation plot failed: {exc}")

    gpx.save()
    return result


def _write_debug_plot(hist, outdir: str, label: str) -> None:
    """
    Dump (x, yobs, ycalc, ydiff) for the histogram so the caller can plot
    or inspect the residuals offline. Writes <outdir>/<label>_pattern.csv.
    """
    os.makedirs(outdir, exist_ok=True)
    x = hist.getdata("x")
    yobs = hist.getdata("yobs")
    try:
        ycalc = hist.getdata("ycalc")
    except Exception:
        ycalc = np.zeros_like(yobs)
    diff = yobs - ycalc
    out = os.path.join(outdir, f"{label}_pattern.csv")
    with open(out, "w") as f:
        f.write("two_theta,yobs,ycalc,diff\n")
        for xi, yo, yc, dv in zip(x, yobs, ycalc, diff):
            f.write(f"{xi:.6f},{yo:.6f},{yc:.6f},{dv:.6f}\n")


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
            wavelength=args.wavelength,
            dmin=args.dmin,
            two_theta_min=args.tmin,
            two_theta_max=args.tmax,
            instprm_path=instprm,
            workdir=tmpdir,
            label=Path(args.data).stem,
            dmax=args.dmax,
            debug_plot=args.debug_plot,
            curation_mode=args.curation_mode,
            baseline_method=args.baseline_method,
        )
    result["file"] = args.data
    return result


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

        for fpath in files:
            try:
                two_theta, intensity = read_xy_file(str(fpath))
                r = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=current_cell,
                    wavelength=args.wavelength,
                    dmin=args.dmin,
                    two_theta_min=args.tmin,
                    two_theta_max=args.tmax,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=fpath.stem,
                    dmax=args.dmax,
                    debug_plot=args.debug_plot,
                    curation_mode=args.curation_mode,
                    baseline_method=args.baseline_method,
                )
                r["file"] = str(fpath)
                # Optional chaining: use refined cell as starting point for
                # next pattern. Off by default to avoid error accumulation
                # across e.g. temperature series with phase transitions.
                if args.chain_cell and r["success"]:
                    current_cell = [
                        r["a"],
                        r["b"],
                        r["c"],
                        r["alpha"],
                        r["beta"],
                        r["gamma"],
                    ]
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

        for pat in patterns:
            label = f"T{pat['temp_c']}C"
            try:
                r = refine_one_pattern(
                    two_theta=pat["two_theta"],
                    intensity=pat["intensity"],
                    space_group=args.space_group,
                    cell_list=current_cell,
                    wavelength=args.wavelength,
                    dmin=args.dmin,
                    two_theta_min=args.tmin,
                    two_theta_max=args.tmax,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=label,
                    dmax=args.dmax,
                    debug_plot=args.debug_plot,
                    curation_mode=args.curation_mode,
                    baseline_method=args.baseline_method,
                )
                r["temp_c"] = pat["temp_c"]
                r["temp_label"] = pat["temp_label"]
                if args.chain_cell and r["success"]:
                    current_cell = [
                        r["a"],
                        r["b"],
                        r["c"],
                        r["alpha"],
                        r["beta"],
                        r["gamma"],
                    ]
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
        "because it propagates errors and may straddle a phase transition.",
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
