#!/usr/bin/env python3
"""
gsas2_pawley.py — GSAS-II Pawley refinement for PXRD data (single file).

Refines lattice parameters from powder XRD data using GSAS-II full-pattern
Pawley extraction. Outputs cell parameters, ESDs, and R-factors as JSON.

This file is self-contained: it includes the GSAS-II kernel (reflection
generation, intensity estimation, refinement driver), the multi-start picker
(cold-start tiebreak + anchor gate), seed perturbation, and all CLI modes
(single / directory / wide-csv). The only local dependency is ``curation.py``
which handles data quality assessment.

GSAS-II path: /root/g2full/GSAS-II/GSASII  (override with --gsas2-path)

Usage:
  # Single pattern:
  python gsas2_pawley.py \\
    --data pattern.xye --space-group "F d -3 m" \\
    --cell "a=5.43,b=5.43,c=5.43" --wavelength 1.5406 -o result.json

  # Directory of patterns (e.g. multi-temperature):
  python gsas2_pawley.py \\
    --data /path/to/patterns/ --space-group "<SG>" \\
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" -o results.json

  # Wide-table CSV (multiple temperatures in one file):
  python gsas2_pawley.py \\
    --data multi_temp.txt --wide-csv --space-group "<SG>" \\
    --cell "a=<A>,b=<B>,c=<C>,beta=<BETA>" -o results.json

Output JSON (single):
  {"success": true, "file": "pattern.xye", "a": 5.431, ..., "wR": 8.5}

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from curation import CurationResult, curate, write_diagnostic_plot  # noqa: E402

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

_CELL_FIELDS = ("a", "b", "c", "alpha", "beta", "gamma", "volume")
_ESD_FIELDS = ("a_esd", "b_esd", "c_esd", "alpha_esd", "beta_esd", "gamma_esd")


# ---------------------------------------------------------------------------
# Cell standardisation — spglib Niggli reduction + reference-cell alignment
# ---------------------------------------------------------------------------

# Axis permutations: (a,b,c,α,β,γ) index mapping for all 6 orderings.
# α = angle(b,c), β = angle(a,c), γ = angle(a,b).
_AXIS_PERMS = [
    (0, 1, 2, 3, 4, 5),  # a  b  c  α β γ  (identity)
    (0, 2, 1, 4, 3, 5),  # a  c  b  β α γ
    (1, 0, 2, 3, 5, 4),  # b  a  c  α γ β
    (1, 2, 0, 5, 3, 4),  # b  c  a  γ α β
    (2, 0, 1, 4, 5, 3),  # c  a  b  β γ α
    (2, 1, 0, 5, 4, 3),  # c  b  a  γ β α
]


def cell_to_lattice(cell: list[float]) -> np.ndarray:
    """[a,b,c,α,β,γ] (Å/deg) → 3×3 row-vector lattice matrix."""
    a, b, c, alpha, beta, gamma = cell
    ar, br, gr = np.radians(alpha), np.radians(beta), np.radians(gamma)
    bx = b * np.cos(gr)
    by = b * np.sin(gr)
    cx = c * np.cos(br)
    cy = c * (np.cos(ar) - np.cos(br) * np.cos(gr)) / np.sin(gr)
    cz = np.sqrt(max(c * c - cx * cx - cy * cy, 0.0))
    return np.array([[a, 0.0, 0.0], [bx, by, 0.0], [cx, cy, cz]])


def lattice_to_cell(L: np.ndarray) -> list[float]:
    """3×3 row-vector lattice matrix → [a,b,c,α,β,γ]."""
    va, vb, vc = L[0], L[1], L[2]
    a, b, c = (np.linalg.norm(v) for v in (va, vb, vc))
    alpha = np.degrees(np.arccos(np.clip(np.dot(vb, vc) / (b * c), -1, 1)))
    beta = np.degrees(np.arccos(np.clip(np.dot(va, vc) / (a * c), -1, 1)))
    gamma = np.degrees(np.arccos(np.clip(np.dot(va, vb) / (a * b), -1, 1)))
    return [float(a), float(b), float(c), float(alpha), float(beta), float(gamma)]


def niggli_reduce_cell(cell: list[float]) -> list[float]:
    """Niggli-reduce a cell using spglib, with fallback to identity."""
    try:
        import spglib

        L = cell_to_lattice(cell)
        L_reduced = spglib.niggli_reduce(L)
        if L_reduced is None:
            return list(cell)
        return lattice_to_cell(L_reduced)
    except ImportError:
        print(
            "[gsas2_pawley] WARNING: spglib not available, "
            "Niggli reduction skipped",
            file=sys.stderr,
        )
        return list(cell)


def _cell_distance_weighted(c1: list[float], c2: list[float]) -> float:
    """Distance between two cells, weighting lengths (Å) and angles (°)."""
    d = 0.0
    for i in range(3):
        d += (c1[i] - c2[i]) ** 2
    for i in range(3, 6):
        d += ((c1[i] - c2[i]) / 10.0) ** 2
    return d


def _enumerate_equivalent_settings(cell: list[float]) -> list[list[float]]:
    """Generate all equivalent cell settings via axis permutation + angle supplement."""
    out: list[list[float]] = []
    for perm in _AXIS_PERMS:
        base = [cell[perm[i]] for i in range(6)]
        queue = [base]
        for ang_idx in (3, 4, 5):
            expanded = []
            for v in queue:
                expanded.append(v)
                if abs(v[ang_idx] - 90.0) > 0.5:
                    alt = list(v)
                    alt[ang_idx] = 180.0 - alt[ang_idx]
                    expanded.append(alt)
            queue = expanded
        out.extend(queue)
    return out


def standardize_cell(
    result: dict, ref_cell: list[float], niggli: bool = False
) -> dict:
    """Standardise refined cell to the same setting as *ref_cell*.

    Enumerates axis permutations × angle-supplement equivalences of
    the refined cell (and optionally its Niggli-reduced form) and picks
    the setting closest to *ref_cell*.

    Operates in-place and returns the same dict.
    """
    cur = [
        result["a"], result["b"], result["c"],
        result["alpha"], result["beta"], result["gamma"],
    ]

    sources = [cur]
    if niggli:
        sources.append(niggli_reduce_cell(cur))

    candidates: list[list[float]] = []
    for src in sources:
        candidates.extend(_enumerate_equivalent_settings(src))

    best = cur
    best_d = _cell_distance_weighted(cur, ref_cell)
    for cand in candidates:
        d = _cell_distance_weighted(cand, ref_cell)
        if d < best_d - 1e-8:
            best_d = d
            best = cand

    if best is not cur:
        result["a"] = round(best[0], 5)
        result["b"] = round(best[1], 5)
        result["c"] = round(best[2], 5)
        result["alpha"] = round(best[3], 4)
        result["beta"] = round(best[4], 4)
        result["gamma"] = round(best[5], 4)

        old_esds = [result.get(f) for f in _ESD_FIELDS]
        if all(e is not None for e in old_esds):
            for perm in _AXIS_PERMS:
                permuted = [cur[perm[i]] for i in range(6)]
                if all(
                    abs(permuted[i] - best[i]) < 0.01
                    or abs(180.0 - permuted[i] - best[i]) < 0.01
                    for i in range(6)
                ):
                    reordered = [old_esds[perm[i]] for i in range(6)]
                    for field, val in zip(_ESD_FIELDS, reordered):
                        result[field] = val
                    break

        result["volume"] = round(cell_volume(best), 4)

    return result


# ---------------------------------------------------------------------------
# GSAS-II kernel helpers (only usable when GSAS-II is on sys.path)
# ---------------------------------------------------------------------------


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
                with_vol = [
                    c for c in successes if c.get("volume") is not None
                ]
                if with_vol:
                    closest = min(
                        with_vol,
                        key=lambda c: abs(
                            float(c["volume"]) - reference_volume
                        ),
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
        standardize_cell(result, ref_cell=cell_list, niggli=standardize_cell_mode == "niggli")
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


# ---------------------------------------------------------------------------
# Multi-pattern helpers (chain-cell promotion, anchor threading)
# ---------------------------------------------------------------------------


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
        "standardize_cell_mode": getattr(args, "standardize_cell", None),
    }


def _accept_chain_promotion(
    prev: dict | None,
    curr: dict,
    wr_max: float,
    vol_jump_max: float,
) -> tuple[bool, str]:
    """Quality gate for cell promotion across a chained temperature series."""
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


def _maybe_promote_cell(
    args,
    last_accepted: dict | None,
    curr: dict,
) -> tuple[list[float] | None, dict | None, str]:
    """Decide whether curr should be promoted to seed the next pattern."""
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


def _chain_anchor_volume(args, last_accepted: dict | None) -> float | None:
    """Volume anchor passed into the multi-start picker for chain-cell runs."""
    if not args.chain_cell or not last_accepted or not last_accepted.get("success"):
        return None
    volume = last_accepted.get("volume")
    return float(volume) if volume is not None else None


def _clone_args_with_direction(args, direction: str):
    values = vars(args).copy()
    values["chain_cell_direction"] = direction
    return argparse.Namespace(**values)


_BOTH_OFF_REF_WR_GATE = 10.0
_BOTH_OFF_REF_DV_FRACTION = 0.01
_BOTH_OFF_REF_CELL_FRACTION = 0.01


def _relative_cell_distance(
    result: dict, ref_cell: list[float] | None
) -> float | None:
    """L1 sum of relative differences over a/b/c plus any non-90° angle.

    Volume is intentionally NOT included: V can match by chance even when
    individual axes diverge (in monoclinic, V = a·b·c·sin(β), so
    different (a, c, β) combinations can give the same volume). This
    metric is the discriminating signal that V proximity misses.
    """
    if ref_cell is None or len(ref_cell) < 6:
        return None
    if any(result.get(k) is None for k in ("a", "b", "c")):
        return None
    d = 0.0
    for i, k in enumerate(("a", "b", "c")):
        if ref_cell[i] > 0:
            d += abs(float(result[k]) - ref_cell[i]) / ref_cell[i]
    for i, k in enumerate(("alpha", "beta", "gamma"), start=3):
        ref_ang = ref_cell[i]
        if abs(ref_ang - 90.0) > 0.5 and result.get(k) is not None and ref_ang > 0:
            d += abs(float(result[k]) - ref_ang) / ref_ang
    return d


def _pick_chain_merge_candidate(
    forward: dict,
    reverse: dict,
    reference_volume: float,
    reference_cell: list[float] | None = None,
    high_wr: float = 10.0,
    wr_tie: float = 3.0,
) -> tuple[dict, dict]:
    """Pick one forward/reverse result using the PXRD merge contract."""
    f_ok = forward.get("success")
    r_ok = reverse.get("success")
    f_wr = forward.get("wR")
    r_wr = reverse.get("wR")
    f_vol = forward.get("volume")
    r_vol = reverse.get("volume")
    f_dv = abs(float(f_vol) - reference_volume) if f_vol is not None else None
    r_dv = abs(float(r_vol) - reference_volume) if r_vol is not None else None
    f_cd = _relative_cell_distance(forward, reference_cell)
    r_cd = _relative_cell_distance(reverse, reference_cell)

    if f_ok and not r_ok:
        source, reason = "forward", "reverse failed"
    elif r_ok and not f_ok:
        source, reason = "reverse", "forward failed"
    elif not f_ok and not r_ok:
        source, reason = "forward", "both failed"
    elif (
        f_wr is not None
        and r_wr is not None
        and f_wr > high_wr
        and r_wr > high_wr
        and abs(f_wr - r_wr) < wr_tie
    ):
        if f_cd is not None and r_cd is not None:
            source = "forward" if f_cd <= r_cd else "reverse"
            reason = "both high-wR/tied; picked closer to reference cell"
        elif f_dv is not None and r_dv is not None:
            source = "forward" if f_dv <= r_dv else "reverse"
            reason = "both high-wR/tied; picked closer to reference volume"
        else:
            source = "forward" if (f_wr or 0) <= (r_wr or 0) else "reverse"
            reason = "both high-wR/tied; missing cell/volume; kept lower wR"
    elif f_wr is not None and r_wr is not None:
        source = "forward" if f_wr <= r_wr else "reverse"
        reason = "picked lower wR"
    else:
        source = "forward"
        reason = "missing wR; kept forward"

    warning: str | None = None
    if (
        f_ok
        and r_ok
        and f_wr is not None
        and r_wr is not None
        and f_wr > _BOTH_OFF_REF_WR_GATE
        and r_wr > _BOTH_OFF_REF_WR_GATE
    ):
        v_off = (
            reference_volume > 0
            and f_dv is not None
            and r_dv is not None
            and (f_dv / reference_volume) > _BOTH_OFF_REF_DV_FRACTION
            and (r_dv / reference_volume) > _BOTH_OFF_REF_DV_FRACTION
        )
        cell_off = (
            f_cd is not None
            and r_cd is not None
            and f_cd > _BOTH_OFF_REF_CELL_FRACTION
            and r_cd > _BOTH_OFF_REF_CELL_FRACTION
        )
        if v_off or cell_off:
            warning = "both_directions_off_ref"

    chosen = dict(forward if source == "forward" else reverse)
    chosen["merge_source"] = source
    chosen["merge_reason"] = reason
    if warning:
        chosen["merge_warning"] = warning
    table_row = {
        "file": forward.get("file") or reverse.get("file"),
        "temp_c": forward.get("temp_c", reverse.get("temp_c")),
        "temp_label": forward.get("temp_label", reverse.get("temp_label")),
        "wR_forward": f_wr,
        "V_forward": f_vol,
        "dV_ref_forward": f_dv,
        "cell_dist_forward": f_cd,
        "wR_reverse": r_wr,
        "V_reverse": r_vol,
        "dV_ref_reverse": r_dv,
        "cell_dist_reverse": r_cd,
        "chosen": source,
        "reason": reason,
        "warning": warning,
    }
    return chosen, table_row


def merge_chain_directions(
    forward_results: list[dict],
    reverse_results: list[dict],
    reference_volume: float,
    reference_cell: list[float] | None = None,
) -> tuple[list[dict], dict]:
    merged: list[dict] = []
    table: list[dict] = []
    for fwd, rev in zip(forward_results, reverse_results):
        chosen, row = _pick_chain_merge_candidate(
            fwd, rev, reference_volume, reference_cell=reference_cell
        )
        merged.append(chosen)
        table.append(row)
    warnings = [
        {
            "file": row.get("file"),
            "issue": row["warning"],
            "wR_forward": row.get("wR_forward"),
            "wR_reverse": row.get("wR_reverse"),
            "dV_ref_forward": row.get("dV_ref_forward"),
            "dV_ref_reverse": row.get("dV_ref_reverse"),
            "cell_dist_forward": row.get("cell_dist_forward"),
            "cell_dist_reverse": row.get("cell_dist_reverse"),
        }
        for row in table
        if row.get("warning")
    ]
    audit: dict = {
        "reference_volume": round(float(reference_volume), 4),
        "table": table,
        "warnings": warnings,
    }
    if reference_cell is not None and len(reference_cell) >= 6:
        audit["reference_cell"] = [round(float(x), 5) for x in reference_cell[:6]]
    return merged, audit


# ---------------------------------------------------------------------------
# Chain self-healing (post-chain outlier rescue)
# ---------------------------------------------------------------------------


def _result_volume(r: dict | None) -> float | None:
    if not r or not r.get("success"):
        return None
    v = r.get("volume")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _result_cell_list(r: dict | None) -> list[float] | None:
    if not r or not r.get("success"):
        return None
    try:
        return [
            float(r["a"]),
            float(r["b"]),
            float(r["c"]),
            float(r["alpha"]),
            float(r["beta"]),
            float(r["gamma"]),
        ]
    except (KeyError, TypeError, ValueError):
        return None


def self_heal_chain_outliers(
    chain_results: list[dict],
    args,
    reference_cell: list[float],
    reference_volume: float,
    v_jump_threshold: float,
    multi_start: int,
) -> tuple[list[dict], dict]:
    """Post-chain rescue for single-pattern wrong-basin convergence.

    For each chain element whose refined volume drifts ``> v_jump_threshold``
    from the average of its successful immediate neighbours, re-refine that
    pattern in-process with a higher ``multi_start`` and a neighbour-average
    cell as the initial guess.  Replace the chain result only if the retry
    produces a volume closer to the neighbour average than the original.

    The helper does NOT consume any chain-position metadata (e.g. temperature)
    beyond list order; it operates purely on the order in which patterns were
    chained, which is what ``--chain-cell`` already encodes.
    """
    n = len(chain_results)
    if n < 3:
        return list(chain_results), {
            "v_jump_threshold": v_jump_threshold,
            "multi_start": multi_start,
            "outliers": [],
            "skipped_reason": f"chain too short ({n}<3) for neighbour rescue",
        }

    healed = list(chain_results)
    audit_entries: list[dict] = []

    successes = [
        (i, _result_volume(r), _result_cell_list(r)) for i, r in enumerate(healed)
    ]
    successes = [(i, v, c) for i, v, c in successes if v is not None and c is not None]
    if len(successes) < 2:
        return healed, {
            "v_jump_threshold": v_jump_threshold,
            "multi_start": multi_start,
            "outliers": [],
            "skipped_reason": "fewer than 2 successful chain elements",
        }

    success_by_idx = {i: (v, c) for i, v, c in successes}

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        for idx in range(n):
            v_orig = _result_volume(healed[idx])
            if v_orig is None:
                continue
            neigh_idxs = [
                j for j in (idx - 1, idx + 1) if j in success_by_idx and j != idx
            ]
            if not neigh_idxs:
                continue
            neigh_vs = [success_by_idx[j][0] for j in neigh_idxs]
            neigh_cells = np.array([success_by_idx[j][1] for j in neigh_idxs])
            v_target = float(np.mean(neigh_vs))
            rel = abs(v_orig - v_target) / max(abs(v_target), 1e-9)
            if rel <= v_jump_threshold:
                continue

            fpath = healed[idx].get("file") or ""
            init_cell = neigh_cells.mean(axis=0).tolist()

            audit_row = {
                "file": fpath,
                "v_original": round(v_orig, 4),
                "v_neighbour_target": round(v_target, 4),
                "rel_jump": round(rel, 4),
                "init_cell_neighbour_avg": [round(x, 5) for x in init_cell],
            }

            if not fpath or not Path(fpath).is_file():
                audit_row["decision"] = "skipped_no_file"
                audit_entries.append(audit_row)
                continue

            try:
                two_theta, intensity = read_xy_file(fpath)
                kwargs = _refine_kwargs_from_args(args)
                kwargs["multi_start"] = multi_start
                retry = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=init_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=Path(fpath).stem + "_self_heal",
                    anchor_volume=v_target,
                    anchor_max_jump=max(args.chain_vol_jump_max, v_jump_threshold * 1.5),
                    reference_volume=reference_volume,
                    **kwargs,
                )
                retry["file"] = fpath
                retry["self_heal_origin"] = "chain_outlier_retry"
                retry_v = _result_volume(retry)
                if retry_v is None:
                    audit_row["decision"] = "skipped_retry_failed"
                    audit_row["retry_error"] = retry.get("error") or "no volume"
                    audit_entries.append(audit_row)
                    continue
                rel_retry = abs(retry_v - v_target) / max(abs(v_target), 1e-9)
                audit_row["v_retry"] = round(retry_v, 4)
                audit_row["rel_retry"] = round(rel_retry, 4)
                audit_row["wR_original"] = healed[idx].get("wR")
                audit_row["wR_retry"] = retry.get("wR")
                if rel_retry < rel:
                    audit_row["decision"] = "replaced"
                    healed[idx] = retry
                else:
                    audit_row["decision"] = "kept_chain"
            except Exception as exc:
                audit_row["decision"] = "skipped_retry_exception"
                audit_row["retry_error"] = str(exc)
            audit_entries.append(audit_row)

    return healed, {
        "v_jump_threshold": v_jump_threshold,
        "multi_start": multi_start,
        "outliers": audit_entries,
    }


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def run_single(args) -> dict:
    """Refine a single PXRD file."""
    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    ref_vol = cell_volume(cell_list)

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
            reference_volume=ref_vol,
            **_refine_kwargs_from_args(args),
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
    ref_vol = cell_volume(cell_list)
    canonical_index = {str(path): idx for idx, path in enumerate(files)}

    if args.chain_cell and args.chain_cell_direction == "both":
        forward = _run_directory_direction(
            _clone_args_with_direction(args, "forward"),
            files=files,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        reverse = _run_directory_direction(
            _clone_args_with_direction(args, "reverse"),
            files=files,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        merged, audit = merge_chain_directions(
            forward["results"],
            reverse["results"],
            ref_vol,
            reference_cell=cell_list,
        )
        out: dict = {
            "success": True,
            "chain_cell_direction": "both",
            "merge_strategy": (
                "high-wR reference-cell proximity (V proximity fallback), "
                "otherwise lower wR"
            ),
            "merge_audit": audit,
            "forward_results": forward["results"],
            "reverse_results": reverse["results"],
            "results": merged,
        }
        if getattr(args, "self_heal_chain", False):
            healed, heal_audit = self_heal_chain_outliers(
                merged,
                args=args,
                reference_cell=cell_list,
                reference_volume=ref_vol,
                v_jump_threshold=args.self_heal_v_jump_threshold,
                multi_start=args.self_heal_multi_start,
            )
            out["results"] = healed
            out["self_heal_audit"] = heal_audit
        return out

    direction_out = _run_directory_direction(
        args,
        files=files,
        cell_list=cell_list,
        ref_vol=ref_vol,
        canonical_index=canonical_index,
    )
    if args.chain_cell and getattr(args, "self_heal_chain", False):
        healed, heal_audit = self_heal_chain_outliers(
            direction_out["results"],
            args=args,
            reference_cell=cell_list,
            reference_volume=ref_vol,
            v_jump_threshold=args.self_heal_v_jump_threshold,
            multi_start=args.self_heal_multi_start,
        )
        direction_out["results"] = healed
        direction_out["self_heal_audit"] = heal_audit
    return direction_out


def _run_directory_direction(
    args,
    files: list[Path],
    cell_list: list[float],
    ref_vol: float,
    canonical_index: dict[str, int],
) -> dict:
    results = []
    run_files = list(files)
    if args.chain_cell and args.chain_cell_direction == "reverse":
        run_files = list(reversed(run_files))

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for fpath in run_files:
            try:
                two_theta, intensity = read_xy_file(str(fpath))
                anchor_volume = _chain_anchor_volume(args, last_accepted)
                r = refine_one_pattern(
                    two_theta=two_theta,
                    intensity=intensity,
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=fpath.stem,
                    anchor_volume=anchor_volume,
                    anchor_max_jump=args.chain_vol_jump_max,
                    reference_volume=ref_vol,
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

    results = sorted(
        results,
        key=lambda r: canonical_index.get(str(r.get("file")), len(canonical_index)),
    )
    return {
        "success": True,
        "chain_cell_direction": args.chain_cell_direction,
        "results": results,
    }


def run_wide_csv(args) -> dict:
    """Parse wide-table CSV (multiple temperatures), refine each column."""
    patterns = parse_wide_csv(args.data)
    if not patterns:
        return {"success": False, "error": "No temperature columns found in wide CSV"}

    cell_dict = parse_cell_string(args.cell)
    cell_list = cell_dict_to_list(cell_dict)
    ref_vol = cell_volume(cell_list)
    canonical_index = {
        (pat["temp_c"], pat["temp_label"]): idx for idx, pat in enumerate(patterns)
    }

    if args.chain_cell and args.chain_cell_direction == "both":
        forward = _run_wide_csv_direction(
            _clone_args_with_direction(args, "forward"),
            patterns=patterns,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        reverse = _run_wide_csv_direction(
            _clone_args_with_direction(args, "reverse"),
            patterns=patterns,
            cell_list=cell_list,
            ref_vol=ref_vol,
            canonical_index=canonical_index,
        )
        merged, audit = merge_chain_directions(
            forward["results"],
            reverse["results"],
            ref_vol,
            reference_cell=cell_list,
        )
        out: dict = {
            "success": True,
            "chain_cell_direction": "both",
            "merge_strategy": (
                "high-wR reference-cell proximity (V proximity fallback), "
                "otherwise lower wR"
            ),
            "merge_audit": audit,
            "forward_results": forward["results"],
            "reverse_results": reverse["results"],
            "results": merged,
        }
        if getattr(args, "self_heal_chain", False):
            healed, heal_audit = self_heal_chain_outliers(
                merged,
                args=args,
                reference_cell=cell_list,
                reference_volume=ref_vol,
                v_jump_threshold=args.self_heal_v_jump_threshold,
                multi_start=args.self_heal_multi_start,
            )
            out["results"] = healed
            out["self_heal_audit"] = heal_audit
        return out

    direction_out = _run_wide_csv_direction(
        args,
        patterns=patterns,
        cell_list=cell_list,
        ref_vol=ref_vol,
        canonical_index=canonical_index,
    )
    if args.chain_cell and getattr(args, "self_heal_chain", False):
        healed, heal_audit = self_heal_chain_outliers(
            direction_out["results"],
            args=args,
            reference_cell=cell_list,
            reference_volume=ref_vol,
            v_jump_threshold=args.self_heal_v_jump_threshold,
            multi_start=args.self_heal_multi_start,
        )
        direction_out["results"] = healed
        direction_out["self_heal_audit"] = heal_audit
    return direction_out


def _run_wide_csv_direction(
    args,
    patterns: list[dict],
    cell_list: list[float],
    ref_vol: float,
    canonical_index: dict[tuple, int],
) -> dict:
    results = []
    run_patterns = list(patterns)
    if args.chain_cell and args.chain_cell_direction == "reverse":
        run_patterns = list(reversed(run_patterns))

    with tempfile.TemporaryDirectory() as tmpdir:
        instprm = args.instprm or make_instprm_file(args.wavelength, tmpdir)
        current_cell = list(cell_list)
        last_accepted: dict | None = None

        for pat in run_patterns:
            label = f"T{pat['temp_c']}C"
            try:
                anchor_volume = _chain_anchor_volume(args, last_accepted)
                r = refine_one_pattern(
                    two_theta=pat["two_theta"],
                    intensity=pat["intensity"],
                    space_group=args.space_group,
                    cell_list=current_cell,
                    instprm_path=instprm,
                    workdir=tmpdir,
                    label=label,
                    anchor_volume=anchor_volume,
                    anchor_max_jump=args.chain_vol_jump_max,
                    reference_volume=ref_vol,
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

    results = sorted(
        results,
        key=lambda r: canonical_index.get(
            (r.get("temp_c"), r.get("temp_label")),
            len(canonical_index),
        ),
    )
    return {
        "success": True,
        "chain_cell_direction": args.chain_cell_direction,
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print("[gsas2_pawley] booting argv=", sys.argv, flush=True)
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
        help='Initial lattice params (e.g. "a=10.0,b=9.5,c=8.2,beta=99.0")',
    )
    ap.add_argument(
        "--wavelength",
        type=float,
        default=1.5406,
        help="X-ray wavelength in Å (default: Cu Kα1 = 1.5406)",
    )
    ap.add_argument("--dmin", type=float, default=2.0)
    ap.add_argument("--dmax", type=float, default=None)
    ap.add_argument("--tmin", type=float, default=None)
    ap.add_argument("--tmax", type=float, default=None)
    ap.add_argument("--instprm", default=None)
    ap.add_argument(
        "--gsas2-path",
        default=DEFAULT_GSAS2_PATH,
        help=f"Path to GSAS-II GSASII directory (default: {DEFAULT_GSAS2_PATH})",
    )
    ap.add_argument("--wide-csv", action="store_true")
    ap.add_argument("--chain-cell", action="store_true")
    ap.add_argument(
        "--chain-cell-direction",
        choices=["forward", "reverse", "both"],
        default="forward",
    )
    ap.add_argument("--chain-wr-max", type=float, default=25.0)
    ap.add_argument("--chain-vol-jump-max", type=float, default=0.03)
    ap.add_argument("--multi-start", type=int, default=1)
    ap.add_argument("--multi-start-seed", type=int, default=42)
    ap.add_argument("--multi-start-len-sigma", type=float, default=0.005)
    ap.add_argument("--multi-start-ang-sigma", type=float, default=0.5)
    ap.add_argument("--debug-plot", default=None)
    ap.add_argument(
        "--curation-mode",
        choices=["off", "auto", "strict"],
        default="auto",
    )
    ap.add_argument(
        "--baseline-method",
        choices=["piecewise_linear", "linear", "mor", "none"],
        default="piecewise_linear",
    )
    ap.add_argument(
        "--standardize-cell",
        choices=["ref", "niggli"],
        default=None,
        help="Post-refinement cell standardisation: 'ref' aligns to the "
        "initial cell via axis-permutation search; 'niggli' additionally "
        "Niggli-reduces (requires spglib) before aligning. Default: off.",
    )
    ap.add_argument(
        "--self-heal-chain",
        dest="self_heal_chain",
        action="store_true",
        default=True,
        help="After a multi-pattern --chain-cell run, scan the result list for "
        "outliers whose volume jumps >--self-heal-v-jump-threshold from the "
        "average of their immediate successful neighbours. Re-refine each "
        "outlier in-process with --self-heal-multi-start restarts and a "
        "neighbour-average cell as initial guess; replace the original only "
        "if the retry lands closer to the neighbour average. Default: on.",
    )
    ap.add_argument(
        "--no-self-heal-chain",
        dest="self_heal_chain",
        action="store_false",
        help="Disable post-chain outlier rescue.",
    )
    ap.add_argument(
        "--self-heal-v-jump-threshold",
        type=float,
        default=0.02,
        help="Relative volume jump above which a chain element is treated as "
        "an outlier candidate for self-healing (default: 0.02 = 2%%).",
    )
    ap.add_argument(
        "--self-heal-multi-start",
        type=int,
        default=5,
        help="Multi-start budget used by the rescue refinement on each "
        "outlier (default: 5).",
    )
    ap.add_argument("-o", "--output", help="Write JSON output to this file")
    args = ap.parse_args()

    setup_gsas2(args.gsas2_path)

    data_path = Path(args.data)

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
    print("[gsas2_pawley] done success=", result.get("success"), flush=True)


if __name__ == "__main__":
    main()
