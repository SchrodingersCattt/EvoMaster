"""Reproducible preprocessing and peak extraction for canonical PXRD traces."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths

from .patterns import MIN_POINTS, PatternInputError, PatternTrace

PROCESSING_PROFILE_VERSION = "standard-1"
CU_K_ALPHA_1_WAVELENGTH = 1.540598


@dataclass(slots=True)
class ProcessedTrace:
    """Preprocessed diffraction arrays and the detected peaks for one trace."""

    trace: PatternTrace
    normalized_intensity: list[float]
    baseline: list[float]
    subtracted_intensity: list[float]
    peaks: list[dict[str, float | int | None]]
    metadata: dict[str, object]
    warnings: list[str]


def process_trace(
    trace: PatternTrace,
    *,
    profile: str = "standard",
    peak_prominence: float | None = None,
    peak_width: float | None = None,
    wavelength: float = CU_K_ALPHA_1_WAVELENGTH,
) -> ProcessedTrace:
    """Create baseline and peak features while preserving measured intensity."""

    if profile not in {"standard", "legacy"}:
        raise PatternInputError(
            "unsupported_processing_profile",
            "Unsupported processing profile. Expected 'standard' or 'legacy'.",
        )
    if len(trace.two_theta) < MIN_POINTS:
        raise PatternInputError(
            "insufficient_points",
            f"Trace {trace.trace_id!r} has fewer than {MIN_POINTS} points.",
        )
    if wavelength <= 0 or not math.isfinite(wavelength):
        raise PatternInputError(
            "invalid_wavelength", "Wavelength must be a positive finite value."
        )

    x = np.asarray(trace.two_theta, dtype=float)
    y = np.asarray(trace.intensity, dtype=float)
    normalized = _normalize_intensity(y)
    baseline = _estimate_baseline(normalized, profile)
    subtracted = np.maximum(normalized - baseline, 0.0)
    prominence = peak_prominence if peak_prominence is not None else 2.0
    width = peak_width if peak_width is not None else 1.0
    if prominence <= 0 or width <= 0:
        raise PatternInputError(
            "invalid_peak_parameters",
            "Peak prominence and width must both be positive.",
        )

    peak_indices, properties = find_peaks(
        subtracted, prominence=prominence, width=width
    )
    widths, _, left_ips, right_ips = peak_widths(
        subtracted, peak_indices, rel_height=0.5
    )
    step = float(np.median(np.diff(x)))
    peaks: list[dict[str, float | int | None]] = []
    for row, index in enumerate(peak_indices):
        position = float(x[index])
        fwhm = float(widths[row] * step)
        d_spacing = _d_spacing(position, wavelength)
        peaks.append(
            {
                "index": int(index),
                "two_theta": position,
                "intensity": float(y[index]),
                "normalized_intensity": float(normalized[index]),
                "subtracted_intensity": float(subtracted[index]),
                "prominence": float(properties["prominences"][row]),
                "fwhm": fwhm,
                "d_spacing": d_spacing,
                "scherrer_size_nm": _scherrer_size_nm(position, fwhm, wavelength),
                "left_two_theta": float(np.interp(left_ips[row], np.arange(len(x)), x)),
                "right_two_theta": float(
                    np.interp(right_ips[row], np.arange(len(x)), x)
                ),
            }
        )

    warnings = list(trace.warnings)
    if not peaks:
        warnings.append("no_peaks_detected")
    warnings.append("scherrer_size_nm_assumes_no_instrumental_broadening")
    return ProcessedTrace(
        trace=trace,
        normalized_intensity=normalized.tolist(),
        baseline=baseline.tolist(),
        subtracted_intensity=subtracted.tolist(),
        peaks=peaks,
        metadata={
            "profile": profile,
            "profile_version": PROCESSING_PROFILE_VERSION,
            "normalization": "max_to_100",
            "baseline_method": "gaussian_lower_envelope",
            "baseline_sigma_points": 12 if profile == "standard" else 18,
            "peak_prominence": prominence,
            "peak_width_points": width,
            "wavelength_angstrom": wavelength,
            "scherrer_shape_factor": 0.89,
        },
        warnings=list(dict.fromkeys(warnings)),
    )


def _normalize_intensity(values: np.ndarray) -> np.ndarray:
    minimum = float(values.min())
    maximum = float(values.max())
    if maximum <= minimum:
        raise PatternInputError(
            "flat_intensity", "The trace has no intensity variation."
        )
    return (values - minimum) / (maximum - minimum) * 100.0


def _estimate_baseline(values: np.ndarray, profile: str) -> np.ndarray:
    sigma = 18 if profile == "legacy" else 12
    smooth = gaussian_filter1d(values, sigma=sigma, mode="nearest")
    baseline = np.minimum(smooth, values)
    return gaussian_filter1d(baseline, sigma=max(2, sigma // 3), mode="nearest")


def _d_spacing(two_theta: float, wavelength: float) -> float | None:
    theta = math.radians(two_theta / 2)
    sine = math.sin(theta)
    if sine <= 0:
        return None
    return wavelength / (2 * sine)


def _scherrer_size_nm(
    two_theta: float, fwhm_degrees: float, wavelength: float
) -> float | None:
    if fwhm_degrees <= 0:
        return None
    theta = math.radians(two_theta / 2)
    beta = math.radians(fwhm_degrees)
    cosine = math.cos(theta)
    if beta <= 0 or cosine <= 0:
        return None
    return 0.89 * wavelength / (beta * cosine) * 0.1
