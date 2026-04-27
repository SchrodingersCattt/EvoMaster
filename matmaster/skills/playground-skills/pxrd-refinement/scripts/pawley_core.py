"""
pawley_core.py — GSAS-II inner kernel for Pawley refinement.

Split out of ``gsas2_pawley.py`` to keep that script under 1000 lines.
This module contains everything that touches GSAS-II (`GSASIIscriptable`,
`GSASIIlattice`, `GSASIImath`, `GSASIIspc`, `GSASIIpwd`) plus the small
helpers that are independent of GSAS-II but tightly coupled to a single
Pawley run (seed perturbation, multi-start audit summary, debug plot).

Public API:

- ``perturb_seed_cells(base_cell, k, len_sigma, ang_sigma, rng_seed)``:
    deterministic seed-cell list for multi-start.
- ``run_pawley_once(...)``: one Pawley refinement against a pre-staged
    .xye file (caller does curation / preprocess / limit clamping). Returns
    a result dict (``success``, cell, ESDs, ``wR``, ``warnings``); failures
    yield ``success=False`` plus an ``error`` field.
- ``summarize_multi_start(candidates)``: compact audit trail for the
    multi-start ensemble.
- ``write_debug_plot(hist, outdir, label)``: per-pattern (2θ, yobs, ycalc,
    diff) CSV for offline plotting.

Lower-level GSAS-II helpers (``generate_pawley_reflections``,
``estimate_pawley_intensities``) are also exported because some callers may
want to build their own driver around them.
"""

from __future__ import annotations

import os
import sys

import numpy as np


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


def perturb_seed_cells(
    base_cell: list[float],
    k: int,
    len_sigma: float,
    ang_sigma: float,
    rng_seed: int,
) -> list[list[float]]:
    """
    Build K seed cells: base + (K-1) deterministic perturbations.

    Length params (a/b/c) are perturbed multiplicatively by N(0, len_sigma);
    angles by additive N(0, ang_sigma) [degrees]. ``rng_seed`` makes the
    perturbation reproducible across runs (crucial for debuggability and
    chain-cell consistency in temperature series).
    """
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
    """
    Run a single GSAS-II Pawley refinement against a pre-staged .xye file.

    Caller is responsible for curation, preprocessing, and limit clamping.
    Returns a result dict with ``success`` (bool), cell parameters, ESDs,
    ``wR``, ``n_reflections``, and a per-run ``warnings`` list. Failures
    (e.g. GSAS-II raising during refinement) yield ``success=False`` plus
    an ``error`` field — the caller can choose to discard the seed and
    keep going across the multi-start ensemble.
    """
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


def summarize_multi_start(candidates: list[dict]) -> list[dict]:
    """Compact multi-start audit trail for the result JSON."""
    summary = []
    for c in candidates:
        entry = {
            "seed_index": c.get("_seed_index"),
            "seed_cell": c.get("_seed_cell"),
            "success": c.get("success"),
            "wR": c.get("wR"),
        }
        if c.get("success"):
            entry["volume"] = c.get("volume")
        else:
            entry["error"] = c.get("error")
        summary.append(entry)
    return summary


def write_debug_plot(hist, outdir: str, label: str) -> None:
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
