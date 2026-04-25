"""
curation.py — PXRD pattern data curation.

Shared pre-refinement pipeline for Pawley/Rietveld/Autoindex:

  1. Auto-detect an "artifact prefix" (smooth monotonic-decay region near
     the low-angle edge) via the slope of the MOR (morphological-opening)
     baseline, and propose a `tmin_cut`.
  2. Clip the pattern to [tmin_cut, tmax].
  3. Fit a low-order baseline on the clipped region (prefer piecewise-linear,
     which is three stitched 1st-order fits — the user's experience shows
     high-order polynomials easily overfit PXRD backgrounds).
  4. Peak-pick the baseline-subtracted signal with SNR>=3 and prominence>=2%
     I_max.
  5. Compute acceptance metrics (dynamic range, peak count, 2θ coverage,
     median residual background, baseline roughness) and assign a
     PASS / WARN / FAIL verdict.

Design principles:
  * Only baseline / peak-pick logic lives here. No GSAS-II, no crystallography.
  * `CurationResult` is a plain dataclass that both `gsas2_pawley.py` and
    `gsas2_autoindex.py` consume.
  * Verdict WARN doesn't stop refinement; FAIL does, because the data is
    unlikely to yield a trustworthy fit.
  * `tmin_cut` can be forced via `tmin_hint` when the user already knows the
    valid range (e.g. instrument calibration below 10°).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class CurationResult:
    tmin_cut: float
    tmax: float
    tth: np.ndarray
    intensity: np.ndarray
    baseline: np.ndarray
    intensity_subtracted: np.ndarray
    baseline_method: str
    dyn_range: float
    peak_count: int
    peak_positions: list[float]
    coverage: dict[str, int]
    bg_median: float
    baseline_roughness: float
    I_max: float
    noise_rms: float
    verdict: str
    reasons: list[str] = field(default_factory=list)

    def summary_dict(self) -> dict:
        return {
            "tmin_cut": round(float(self.tmin_cut), 3),
            "tmax": round(float(self.tmax), 3),
            "baseline_method": self.baseline_method,
            "dyn_range": round(float(self.dyn_range), 2),
            "peak_count": int(self.peak_count),
            "peak_positions": [round(float(p), 3) for p in self.peak_positions],
            "coverage": self.coverage,
            "bg_median": round(float(self.bg_median), 5),
            "baseline_roughness": round(float(self.baseline_roughness), 5),
            "I_max": round(float(self.I_max), 5),
            "noise_rms": round(float(self.noise_rms), 5),
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


def _rolling_min_smoothed(tth: np.ndarray, I: np.ndarray,
                          half_window_deg: float = 3.0) -> np.ndarray:
    """Rolling-minimum baseline followed by a box-average smoother.

    Used internally for artifact-end detection. We deliberately avoid
    `pybaselines.Baseline.mor` here because its output varies across pybaselines
    versions and can over-smooth the monotonic artifact descent, which breaks
    slope-based artifact detection (seen on the Bohrium xrd-app image where
    pybaselines MOR flattens the 5–13° hump and the detector stops returning a
    sensible tmin_cut). Plain rolling-minimum is deterministic, dependency-free
    and empirically produces a clearly kinked slope at the artifact end.
    """
    dtth = float(np.median(np.diff(tth)))
    hw = max(10, int(half_window_deg / dtth))
    n = len(I)
    bl = np.empty(n)
    for i in range(n):
        lo = max(0, i - hw)
        hi = min(n, i + hw + 1)
        bl[i] = np.min(I[lo:hi])
    kern = np.ones(hw) / hw
    pad = hw
    padded = np.concatenate([np.full(pad, bl[0]), bl, np.full(pad, bl[-1])])
    return np.convolve(padded, kern, mode="same")[pad:pad + n]


def _mor_baseline(tth: np.ndarray, I: np.ndarray, half_window_deg: float = 3.0) -> np.ndarray:
    """Morphological-opening-style baseline for plotting / generic use.

    Uses `pybaselines.Baseline.mor` when available (smoother, more accurate for
    most patterns); falls back to rolling-minimum otherwise. Kept for API
    continuity — artifact detection intentionally uses the rolling-minimum
    form directly for reproducibility across environments.
    """
    dtth = float(np.median(np.diff(tth)))
    hw = max(10, int(half_window_deg / dtth))
    try:
        from pybaselines import Baseline
        return Baseline(x_data=tth).mor(I, half_window=hw)[0]
    except Exception:
        return _rolling_min_smoothed(tth, I, half_window_deg)


def detect_artifact_end(
    tth: np.ndarray,
    I: np.ndarray,
    half_window_deg: float = 3.0,
    slope_k: float = 1.0,
    margin_deg: float = 1.0,
    sustain_deg: float = 2.0,
    search_max_deg: float = 20.0,
) -> float:
    """Return the 2θ where the low-angle artifact prefix ends.

    Strategy: compute a rolling-min baseline, look for the first sustained
    region where |d(baseline)/d(2θ)| < slope_k * avg_slope where
    avg_slope = (max(I) - min(I)) / (tth[-1] - tth[0]). Bragg-dominated
    regions have slope << avg_slope; a smooth decaying artifact has slope
    >> avg_slope. Requiring the flat condition to persist for `sustain_deg`
    avoids triggering on the dip between two adjacent peaks.

    Tunables:
      slope_k    — multiplier on the average slope; 1.0 is the default
                   sweet spot (tested on DFT-simulated VT-PXRD).
      sustain_deg— how long the slope must stay flat before calling the
                   region "post-artifact" (2° is robust to peak spacing).
      search_max_deg — cap so we don't clip away half the pattern if the
                       whole background has a mild positive tilt.
    """
    bl = _rolling_min_smoothed(tth, I, half_window_deg)
    dbl = np.gradient(bl, tth)
    span = float(tth[-1] - tth[0])
    avg_slope = float(I.max() - I.min()) / max(span, 1e-6)
    thr = slope_k * avg_slope
    dtth = float(np.median(np.diff(tth)))
    sustain = max(3, int(sustain_deg / dtth))
    search_limit_idx = int(np.searchsorted(tth, tth[0] + search_max_deg))
    # Only clip when we actually see an above-threshold slope region first
    # (= real monotonic artifact). Otherwise clean data gets clipped by
    # `margin_deg` unnecessarily.
    seen_hot = False
    for i in range(len(dbl)):
        if i > search_limit_idx:
            break
        if abs(dbl[i]) >= thr:
            seen_hot = True
            continue
        if not seen_hot:
            continue
        end = min(i + sustain, len(dbl))
        if np.all(np.abs(dbl[i:end]) < thr):
            return float(tth[i] + margin_deg)
    return float(tth[0])


def _baseline_linear(tth: np.ndarray, I: np.ndarray,
                     n_iter: int = 5, pct: float = 25.0,
                     k_sigma: float = 2.5) -> np.ndarray:
    idx = np.argsort(I)[: max(10, int(pct * len(I) / 100))]
    coef = np.polyfit(tth[idx], I[idx], 1)
    for _ in range(n_iter):
        pred = np.polyval(coef, tth)
        res = I - pred
        sigma = 1.4826 * np.median(np.abs(res - np.median(res)))
        mask = res < k_sigma * sigma
        if mask.sum() < 10:
            break
        coef = np.polyfit(tth[mask], I[mask], 1)
    return np.polyval(coef, tth)


def _baseline_piecewise_linear(tth: np.ndarray, I: np.ndarray,
                               n_pieces: int = 3, **kw) -> np.ndarray:
    edges = np.linspace(tth[0], tth[-1], n_pieces + 1)
    base = np.zeros_like(I, dtype=float)
    for i in range(n_pieces):
        m = (tth >= edges[i]) & (tth <= edges[i + 1])
        if m.sum() < 10:
            base[m] = float(np.median(I[m])) if m.any() else 0.0
            continue
        base[m] = _baseline_linear(tth[m], I[m], **kw)
    dtth = float(np.median(np.diff(tth)))
    w = max(3, int(1.0 / dtth))
    kern = np.ones(w) / w
    pad = w
    padded = np.concatenate([np.full(pad, base[0]), base, np.full(pad, base[-1])])
    return np.convolve(padded, kern, mode="same")[pad:pad + len(base)]


def fit_baseline(tth: np.ndarray, I: np.ndarray, method: str) -> np.ndarray:
    """Dispatch to a baseline method.

    Preferred: 'piecewise_linear' — three stitched 1st-order fits. Robust,
    low-order, follows gentle curvature without overfitting real peaks.
    """
    if method == "linear":
        return _baseline_linear(tth, I)
    if method == "piecewise_linear":
        return _baseline_piecewise_linear(tth, I, n_pieces=3)
    if method == "mor":
        return _mor_baseline(tth, I, half_window_deg=3.0)
    if method == "none":
        return np.zeros_like(I, dtype=float)
    raise ValueError(f"Unknown baseline method: {method!r}")


def _peak_pick(tth: np.ndarray, I_sub: np.ndarray,
               prom_snr: float = 3.0, prom_frac_Imax: float = 0.02):
    from scipy.signal import find_peaks
    med = float(np.median(I_sub))
    mad = float(np.median(np.abs(I_sub - med)))
    noise = 1.4826 * mad
    I_max = float(I_sub.max())
    prom = max(prom_snr * noise, prom_frac_Imax * I_max)
    dist = max(1, int(len(tth) / 500))
    idx, _ = find_peaks(I_sub, prominence=prom, distance=dist)
    return idx, noise, I_max


def _compute_metrics(
    tth: np.ndarray,
    I_sub: np.ndarray,
    baseline: np.ndarray,
    peak_idx: np.ndarray,
    noise: float,
    I_max: float,
    bins: tuple[float, float, float],
):
    b0, b1, b2 = bins
    tth_p = tth[peak_idx]
    coverage = {
        f"[{b0:g},{b1:g})": int(((tth_p >= b0) & (tth_p < b1)).sum()),
        f"[{b1:g},{b2:g})": int(((tth_p >= b1) & (tth_p < b2)).sum()),
        f"[{b2:g},+)":      int((tth_p >= b2).sum()),
    }
    dd = np.diff(baseline, n=2)
    roughness = float(np.std(dd) / max(I_max, 1e-9))
    peak_mask = np.zeros_like(I_sub, dtype=bool)
    half = max(1, int(len(tth) / 200))
    for i in peak_idx:
        peak_mask[max(0, i - half):min(len(tth), i + half)] = True
    bg_median = float(np.median(I_sub[~peak_mask])) if (~peak_mask).any() else 0.0
    dyn_range = float(I_max / max(noise, 1e-9))
    return {
        "dyn_range": dyn_range,
        "coverage": coverage,
        "baseline_roughness": roughness,
        "bg_median": bg_median,
        "peak_count": int(len(peak_idx)),
    }


def _make_verdict(m: dict, I_max: float, min_peaks: int, min_dyn_range: float,
                  min_coverage_per_bin: int) -> tuple[str, list[str]]:
    v = "PASS"
    reasons: list[str] = []
    if m["dyn_range"] < min_dyn_range:
        reasons.append(
            f"dyn_range {m['dyn_range']:.1f} < {min_dyn_range:g}"
        )
        v = "FAIL"
    if m["peak_count"] < min_peaks:
        reasons.append(f"peak_count {m['peak_count']} < {min_peaks}")
        v = "FAIL"
    cov_vals = list(m["coverage"].values())
    if cov_vals and min(cov_vals) < min_coverage_per_bin:
        reasons.append(
            f"coverage thin per-bin: {m['coverage']} "
            f"(want ≥ {min_coverage_per_bin} each)"
        )
        if v != "FAIL":
            v = "WARN"
    if abs(m["bg_median"]) > 0.05 * I_max:
        reasons.append(
            f"|bg_median| {abs(m['bg_median']):.3f} > 5% I_max "
            f"{I_max:.3f} (baseline may overshoot)"
        )
        if v != "FAIL":
            v = "WARN"
    if m["baseline_roughness"] > 0.05:
        reasons.append(
            f"baseline roughness {m['baseline_roughness']:.3f} > 0.05 "
            f"(baseline overfitting real peaks?)"
        )
        if v != "FAIL":
            v = "WARN"
    return v, reasons


def curate(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    baseline_method: str = "piecewise_linear",
    tmin_hint: Optional[float] = None,
    tmax_hint: Optional[float] = None,
    auto_detect_artifact: bool = True,
    coverage_bins: tuple[float, float, float] = (15.0, 25.0, 40.0),
    min_peaks: int = 12,
    min_dyn_range: float = 10.0,
    min_coverage_per_bin: int = 2,
) -> CurationResult:
    """Run the full curation pipeline on a single pattern.

    Parameters
    ----------
    two_theta, intensity : np.ndarray
        The raw pattern (any physical units; only relative scale matters).
    baseline_method : str
        One of 'piecewise_linear' (default, recommended), 'linear', 'mor',
        'none'.
    tmin_hint, tmax_hint : float or None
        Force a specific clipping range. If None:
          * tmin_hint=None + auto_detect_artifact=True → detect via MOR-slope
          * tmax_hint=None → use data's max 2θ
    auto_detect_artifact : bool
        Enable/disable automatic artifact prefix detection.
    coverage_bins : (b0, b1, b2)
        2θ bin boundaries for the coverage metric. Peaks are counted in
        [b0, b1), [b1, b2), [b2, +∞).
    """
    tth_all = np.asarray(two_theta, dtype=float)
    I_all = np.asarray(intensity, dtype=float)
    order = np.argsort(tth_all)
    tth_all = tth_all[order]
    I_all = I_all[order]

    tmax = float(tmax_hint) if tmax_hint is not None else float(tth_all[-1])
    if tmin_hint is not None:
        tmin_cut = float(tmin_hint)
    elif auto_detect_artifact:
        tmin_cut = detect_artifact_end(tth_all, I_all)
    else:
        tmin_cut = float(tth_all[0])

    m = (tth_all >= tmin_cut) & (tth_all <= tmax)
    tth = tth_all[m]
    I = I_all[m]
    if len(tth) < 50:
        raise ValueError(
            f"Too few points after clipping to [{tmin_cut:.3f}, {tmax:.3f}]: "
            f"{len(tth)} (need >=50)."
        )

    baseline = fit_baseline(tth, I, baseline_method)
    I_sub = I - baseline
    peak_idx, noise, I_max = _peak_pick(tth, I_sub)
    metrics = _compute_metrics(tth, I_sub, baseline, peak_idx, noise, I_max,
                               coverage_bins)
    verdict, reasons = _make_verdict(
        metrics, I_max, min_peaks=min_peaks,
        min_dyn_range=min_dyn_range,
        min_coverage_per_bin=min_coverage_per_bin,
    )
    if tmin_cut - float(tth_all[0]) > 1.0:
        reasons.append(
            f"clipped {tmin_cut - float(tth_all[0]):.1f}° of artifact prefix "
            f"(tmin_cut={tmin_cut:.2f}°)"
        )

    return CurationResult(
        tmin_cut=tmin_cut, tmax=tmax,
        tth=tth, intensity=I, baseline=baseline, intensity_subtracted=I_sub,
        baseline_method=baseline_method,
        dyn_range=metrics["dyn_range"],
        peak_count=metrics["peak_count"],
        peak_positions=[float(p) for p in tth[peak_idx]],
        coverage=metrics["coverage"],
        bg_median=metrics["bg_median"],
        baseline_roughness=metrics["baseline_roughness"],
        I_max=I_max, noise_rms=float(noise),
        verdict=verdict, reasons=reasons,
    )


def write_diagnostic_plot(cr: CurationResult, raw_tth: np.ndarray,
                          raw_I: np.ndarray, out_png: str,
                          title: str = "") -> None:
    """Dump a two-panel PNG: raw+baseline on top, subtracted+peaks on bottom."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.12},
    )
    axes[0].plot(raw_tth, raw_I, lw=0.7, color="black", label="raw")
    axes[0].axvline(cr.tmin_cut, color="red", ls="--", lw=1,
                    label=f"tmin_cut={cr.tmin_cut:.2f}°")
    axes[0].plot(cr.tth, cr.baseline, lw=1.0, color="tab:orange",
                 label=f"{cr.baseline_method} baseline")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].set_ylabel("I raw")
    axes[0].set_title(
        (title + " " if title else "")
        + f"curation: peaks={cr.peak_count} dyn={cr.dyn_range:.1f} "
        + f"verdict={cr.verdict}",
        fontsize=10,
    )

    axes[1].plot(cr.tth, cr.intensity_subtracted, lw=0.7, color="tab:blue",
                 label="I - baseline")
    for p in cr.peak_positions:
        axes[1].axvline(p, color="gray", lw=0.3, alpha=0.5)
    axes[1].axhline(0, color="gray", lw=0.5, ls=":")
    axes[1].set_xlabel("2θ (°)")
    axes[1].set_ylabel("I - baseline")
    axes[1].legend(loc="upper right", fontsize=8)

    plt.tight_layout()
    plt.savefig(out_png, dpi=140, bbox_inches="tight")
    plt.close(fig)


__all__ = [
    "CurationResult",
    "curate",
    "detect_artifact_end",
    "fit_baseline",
    "write_diagnostic_plot",
]
