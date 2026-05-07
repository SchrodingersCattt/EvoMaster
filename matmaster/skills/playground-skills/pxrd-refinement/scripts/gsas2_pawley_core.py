"""Core GSAS-II Pawley refinement helpers for gsas2_pawley.py."""

from __future__ import annotations

import csv
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curation import CurationResult, curate, write_diagnostic_plot  # noqa: E402
from gsas2_pawley_cell import _CELL_FIELDS, _ESD_FIELDS, standardize_cell  # noqa: E402

DEFAULT_GSAS2_PATH = "/root/g2full/GSAS-II/GSASII"

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

CELL_PARAMS = {
    "cubic": ["a"],
    "tetragonal": ["a", "c"],
    "hexagonal": ["a", "c"],
    "trigonal": ["a", "c"],
    "orthorhombic": ["a", "b", "c"],
    "monoclinic": ["a", "b", "c", "beta"],
    "triclinic": ["a", "b", "c", "alpha", "beta", "gamma"],
}

COLD_START_WR_FLOOR = 10.0
COLD_START_WR_SPREAD = 1.5


def generate_pawley_reflections(
    phase_data: dict, dmin: float, dmax: float | None = None
) -> list:
    """Generate Pawley reflection list (mirrors GSAS-II 'Pawley create')."""
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
    """Initialize Pawley reflection intensities from observed pattern."""
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


def write_debug_plot(hist, outdir: str, label: str) -> None:
    """Dump (x, yobs, ycalc, ydiff) CSV for offline plotting."""
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


def run_pawley_once(
    xye_path: str,
    instprm_path: str,
    space_group: str,
    cell_list: list[float],
    dmin: float,
    dmax: float | None,
    lim_lo: float,
    lim_hi: float,
    workdir: str,
    label: str,
    debug_plot: str | None = None,
) -> dict:
    """Run a single GSAS-II Pawley refinement against a pre-staged .xye."""
    import GSASIIscriptable as G2sc

    G2sc.SetPrintLevel("warn")
    warnings: list[str] = []

    gpx_path = os.path.join(workdir, f"{label}.gpx")
    try:
        gpx = G2sc.G2Project(newgpx=gpx_path)
        hist = gpx.add_powder_histogram(xye_path, iparams=instprm_path)
        phase = gpx.add_phase(
            phasename="phase",
            spacegroup=space_group,
            cell=cell_list,
            histograms=[hist],
        )
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
                f"high wR ({wR:.2f}%); refinement likely poor — check initial "
                f"cell, space group, peak-shape, or 2θ range"
            )

        result: dict = {
            "success": True,
            "a": round(cell["length_a"], 5),
            "b": round(cell["length_b"], 5),
            "c": round(cell["length_c"], 5),
            "alpha": round(cell["angle_alpha"], 4),
            "beta": round(cell["angle_beta"], 4),
            "gamma": round(cell["angle_gamma"], 4),
            "volume": round(cell["volume"], 4),
            "wR": round(wR, 2) if wR is not None else None,
            "n_reflections": n_reflections,
            "warnings": warnings,
        }

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
                write_debug_plot(hist, debug_plot, label)
            except Exception as exc:
                warnings.append(f"debug plot failed: {exc}")

        gpx.save()
        return result
    except Exception as exc:
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "wR": None,
            "warnings": warnings,
        }


# ---------------------------------------------------------------------------
# Multi-start: seed perturbation, picker, audit summary
# ---------------------------------------------------------------------------


def perturb_seed_cells(
    base_cell: list[float],
    k: int,
    len_sigma: float,
    ang_sigma: float,
    rng_seed: int,
) -> list[list[float]]:
    """Build K seed cells: base + (K-1) deterministic perturbations."""
    if k <= 1:
        return [list(base_cell)]
    rng = np.random.default_rng(rng_seed)
    seeds: list[list[float]] = [list(base_cell)]
    for _ in range(k - 1):
        a, b, c, alpha, beta, gamma = base_cell
        a2 = a * float(np.exp(rng.normal(0.0, len_sigma)))
        b2 = b * float(np.exp(rng.normal(0.0, len_sigma)))
        c2 = c * float(np.exp(rng.normal(0.0, len_sigma)))
        alpha2 = float(np.clip(alpha + rng.normal(0.0, ang_sigma), 60.0, 120.0))
        beta2 = float(np.clip(beta + rng.normal(0.0, ang_sigma), 60.0, 120.0))
        gamma2 = float(np.clip(gamma + rng.normal(0.0, ang_sigma), 60.0, 120.0))
        seeds.append([a2, b2, c2, alpha2, beta2, gamma2])
    return seeds


def _is_cold_start_regime(successes: list[dict]) -> bool:
    """True iff every successful seed's wR is high *and* clustered."""
    if len(successes) < 2:
        return False
    wRs = [float(c["wR"]) for c in successes if c.get("wR") is not None]
    if len(wRs) < 2:
        return False
    return (
        min(wRs) > COLD_START_WR_FLOOR and (max(wRs) - min(wRs)) < COLD_START_WR_SPREAD
    )


REF_VOL_WR_TOLERANCE = 3.0  # % — wR penalty budget for reference-volume proximity


def pick_best_candidate(
    candidates: list[dict],
    *,
    anchor_volume: float | None = None,
    anchor_max_jump: float = 0.05,
    reference_volume: float | None = None,
) -> tuple[dict, str]:
    """Pick the best Pawley candidate from a multi-start ensemble.

    Returns ``(picked, reason)``.

    Default rule: minimum wR among successful seeds.

    Reference-volume proximity (first point, no chain anchor): when
    ``reference_volume`` is set and ``anchor_volume`` is None, candidates
    whose wR is within ``REF_VOL_WR_TOLERANCE`` of the minimum are
    short-listed, and the one closest to ``reference_volume`` wins. This
    prevents a low-wR wrong-basin solution from beating a physically
    correct one when the data is noisy.

    Cold-start tiebreak: when every successful seed has wR above
    COLD_START_WR_FLOOR and the spread is below COLD_START_WR_SPREAD,
    fall back to seed_index=0 (the prompt initial cell).

    Chain-cell anchor gate: when ``anchor_volume`` is provided, discard
    seeds whose refined volume differs by more than ``anchor_max_jump``.
    If the anchor rejects every seed, fall through to the default picker.
    """
    if not candidates:
        raise ValueError("pick_best_candidate: empty candidate list")

    successes = [c for c in candidates if c.get("success") and c.get("wR") is not None]
    if not successes:
        return candidates[0], "all candidates failed"

    anchor_reason = ""
    if anchor_volume is not None and anchor_volume > 0:
        in_basin = []
        rejected = 0
        for c in successes:
            volume = c.get("volume")
            if volume is None:
                rejected += 1
                continue
            jump = abs(float(volume) - anchor_volume) / anchor_volume
            if jump <= anchor_max_jump:
                in_basin.append(c)
            else:
                rejected += 1

        if in_basin:
            successes = in_basin
            anchor_reason = (
                f"; anchor V={anchor_volume:.2f}, gate ±{anchor_max_jump * 100:.1f}%, "
                f"{len(in_basin)} survived, {rejected} rejected"
            )
        else:
            anchor_reason = (
                f"; anchor V={anchor_volume:.2f} rejected all seeds at "
                f"±{anchor_max_jump * 100:.1f}%, falling through to default picker"
            )

    if len(successes) == 1:
        only = successes[0]
        return only, (
            f"only successful seed (seed_index={only.get('_seed_index')})"
            f"{anchor_reason}"
        )

    # Reference-volume proximity: when no chain anchor exists, prefer
    # candidates close to the initial cell's volume over pure min-wR.
    # This fires in both cold-start and normal regimes, as long as there
    # is no chain anchor (first point in a chain or single-pattern mode).
    if (
        reference_volume is not None
        and reference_volume > 0
        and anchor_volume is None
        and len(successes) >= 2
    ):
        min_wR = min(float(c["wR"]) for c in successes)
        near_best = [
            c
            for c in successes
            if float(c["wR"]) <= min_wR + REF_VOL_WR_TOLERANCE
            and c.get("volume") is not None
        ]
        if near_best:
            closest = min(
                near_best,
                key=lambda c: abs(float(c["volume"]) - reference_volume),
            )
            min_wR_cand = min(successes, key=lambda c: float(c["wR"]))
            if closest is not min_wR_cand:
                return closest, (
                    f"ref-vol proximity: seed_index={closest.get('_seed_index')}, "
                    f"wR={closest['wR']:.2f}%, V={closest['volume']:.2f} "
                    f"(ref V={reference_volume:.2f}, min-wR={min_wR:.2f}%, "
                    f"tolerance={REF_VOL_WR_TOLERANCE:.1f}%){anchor_reason}"
                )

    if _is_cold_start_regime(successes):
        wRs = sorted(float(c["wR"]) for c in successes)
        spread_str = (
            f"cold-start tiebreak: all seeds wR > {COLD_START_WR_FLOOR:.1f}% "
            f"and spread {wRs[-1] - wRs[0]:.2f}% < {COLD_START_WR_SPREAD:.1f}%"
        )
        if anchor_volume is not None:
            if (
                reference_volume is not None
                and reference_volume > 0
                and len(successes) >= 2
            ):
                with_vol = [c for c in successes if c.get("volume") is not None]
                if with_vol:
                    closest = min(
                        with_vol,
                        key=lambda c: abs(float(c["volume"]) - reference_volume),
                    )
                    return closest, (
                        f"{spread_str}; anchor + ref-vol proximity: "
                        f"seed_index={closest.get('_seed_index')}, "
                        f"wR={closest['wR']:.2f}%, V={closest['volume']:.2f} "
                        f"(ref V={reference_volume:.2f}){anchor_reason}"
                    )
            best_anchor = min(successes, key=lambda c: float(c["wR"]))
            return best_anchor, (
                f"{spread_str}; anchor active → min-wR among anchor-surviving seeds "
                f"(seed_index={best_anchor.get('_seed_index')}, "
                f"wR={best_anchor['wR']:.2f}%){anchor_reason}"
            )
        seed0 = next(
            (c for c in successes if c.get("_seed_index") == 0),
            None,
        )
        if seed0 is not None:
            return seed0, (
                f"{spread_str}; "
                f"preferring seed_index=0 (prompt initial cell) over min-wR"
                f"{anchor_reason}"
            )

    best = min(successes, key=lambda c: float(c["wR"]))
    return best, (
        f"min-wR (seed_index={best.get('_seed_index')}, wR={best['wR']:.2f}%)"
        f"{anchor_reason}"
    )


def summarize_multi_start(candidates: list[dict]) -> list[dict]:
    """Compact multi-start audit trail preserving full refined cells."""
    summary = []
    for c in candidates:
        entry = {
            "seed_index": c.get("_seed_index"),
            "seed_cell": c.get("_seed_cell"),
            "success": c.get("success"),
            "wR": c.get("wR"),
        }
        if c.get("success"):
            for k in _CELL_FIELDS:
                if k in c:
                    entry[k] = c[k]
            for k in _ESD_FIELDS:
                if k in c:
                    entry[k] = c[k]
            if "n_reflections" in c:
                entry["n_reflections"] = c["n_reflections"]
        else:
            entry["error"] = c.get("error")
        summary.append(entry)
    return summary


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


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
    """Adaptive preprocessing: DFT-style flat data → scale up; real counts → passthrough."""
    if warnings is None:
        warnings = []

    p5 = float(np.percentile(intensity, 5))
    pmax = float(np.max(intensity))
    pmin = float(np.min(intensity))

    denom = max(p5, 1e-9)
    dyn_range = (pmax - pmin) / denom if denom > 0 else float("inf")

    if dyn_range < 10.0:
        baseline = p5
        scale = 1e4
        y = (intensity - baseline) * scale
        mode = "dft_scaled"
        info = {"baseline": baseline, "scale": scale}
    else:
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


def parse_wide_csv(filepath: str) -> list[dict]:
    """Parse a wide-table CSV with paired (angle, intensity) columns per temperature."""
    with open(filepath) as f:
        reader = csv.reader(f)
        header = next(reader)

    temp_cols = []
    for i in range(0, len(header), 2):
        if i + 1 >= len(header):
            break
        label = header[i + 1].strip()
        m = re.search(r"(\d+)", label)
        if m:
            temp_c = int(m.group(1))
            temp_cols.append((i, i + 1, temp_c, label))

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
    """Parse 'a=5.43,b=5.43,c=5.43' → dict."""
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


def cell_volume(cell_list: list[float]) -> float:
    """Compute unit-cell volume from [a,b,c,alpha,beta,gamma] in Å/degrees."""
    a, b, c, alpha, beta, gamma = cell_list
    ar, br, gr = np.radians(alpha), np.radians(beta), np.radians(gamma)
    ca, cb, cg = np.cos(ar), np.cos(br), np.cos(gr)
    return a * b * c * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg)


# ---------------------------------------------------------------------------
# Single-pattern refinement (entry point for all modes)
# ---------------------------------------------------------------------------


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
    anchor_volume: float | None = None,
    anchor_max_jump: float = 0.05,
    reference_volume: float | None = None,
    standardize_cell_mode: str | None = None,
) -> dict:
    """Run GSAS-II Pawley refinement on a single pattern (multi-start capable).

    *standardize_cell_mode*: ``None`` (off), ``"ref"`` (align to ref_cell
    via axis-permutation search), or ``"niggli"`` (Niggli-reduce first,
    then align).
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

    best, pick_reason = pick_best_candidate(
        candidates,
        anchor_volume=anchor_volume,
        anchor_max_jump=anchor_max_jump,
        reference_volume=reference_volume,
    )
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
    if standardize_cell_mode:
        result = standardize_cell(
            result, ref_cell=cell_list, niggli=standardize_cell_mode == "niggli"
        )
    if anchor_volume is not None:
        result["anchor_volume"] = round(float(anchor_volume), 4)
        result["anchor_max_jump"] = anchor_max_jump
    for k_esd in _ESD_FIELDS:
        if k_esd in best:
            result[k_esd] = best[k_esd]
    if curation is not None:
        result["curation"] = curation.summary_dict()
    if k > 1:
        result["multi_start"] = summarize_multi_start(candidates)
        result["multi_start_pick"] = {
            "seed_index": best.get("_seed_index"),
            "reason": pick_reason,
        }
        chosen = best["_seed_index"]
        wRs = [(c.get("wR") if c.get("success") else None) for c in candidates]
        result["warnings"].append(
            f"multi-start picked seed {chosen}/{k - 1} (wR list={wRs}); {pick_reason}"
        )

    if debug_plot and curation is not None:
        try:
            png = os.path.join(debug_plot, f"{label}_curation.png")
            write_diagnostic_plot(curation, two_theta, intensity, png, title=label)
        except Exception as exc:
            result["warnings"].append(f"curation plot failed: {exc}")

    return result
