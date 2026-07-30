"""CIF-based ideal powder-XRD simulation and experimental comparison."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import comparison_chart, reference_stick_series
from .patterns import PatternInputError, PatternTrace

RADIATION_WAVELENGTHS = {
    "cu-ka1": 1.540598,
    "cu-ka2": 1.544426,
    "cu-ka": 1.541874,
    "cu-kb": 1.3922,
}
DEFAULT_RADIATION = "cu-ka1"


@dataclass(slots=True)
class SimulatedPattern:
    """Ideal Bragg-stick PXRD pattern derived from a crystal structure."""

    two_theta: list[float]
    intensity: list[float]
    d_spacing: list[float]
    hkl: list[str]
    radiation: str
    wavelength: float
    two_theta_min: float
    two_theta_max: float


def resolve_radiation(
    radiation: str | None, wavelength: float | None
) -> tuple[str, float]:
    """Resolve a named radiation source or an explicit wavelength."""

    if wavelength is not None:
        if wavelength <= 0 or not math.isfinite(wavelength):
            raise PatternInputError(
                "invalid_wavelength", "Wavelength must be a positive finite value."
            )
        return radiation or "custom", wavelength
    name = (radiation or DEFAULT_RADIATION).lower().replace("_", "-")
    if name not in RADIATION_WAVELENGTHS:
        raise PatternInputError(
            "unsupported_radiation",
            "Unsupported radiation. Expected one of: "
            + ", ".join(sorted(RADIATION_WAVELENGTHS)),
        )
    return name, RADIATION_WAVELENGTHS[name]


def simulate_cif(
    cif_path: Path,
    *,
    radiation: str | None = None,
    wavelength: float | None = None,
    two_theta_min: float = 5.0,
    two_theta_max: float = 90.0,
) -> SimulatedPattern:
    """Calculate an ideal powder pattern using pymatgen's XRDCalculator."""

    if not cif_path.is_file():
        raise PatternInputError("missing_cif", f"CIF file not found: {cif_path.name}")
    if not 0 <= two_theta_min < two_theta_max <= 180:
        raise PatternInputError(
            "invalid_two_theta_range",
            "The 2Theta range must satisfy 0 <= minimum < maximum <= 180.",
        )
    resolved_radiation, resolved_wavelength = resolve_radiation(radiation, wavelength)
    try:
        from pymatgen.analysis.diffraction.xrd import XRDCalculator
        from pymatgen.core import Structure

        structure = Structure.from_file(cif_path)
        pattern = XRDCalculator(wavelength=resolved_wavelength).get_pattern(
            structure,
            two_theta_range=(two_theta_min, two_theta_max),
        )
    except PatternInputError:
        raise
    except Exception as exc:
        raise PatternInputError(
            "invalid_cif", "Could not read the CIF or calculate its ideal PXRD pattern."
        ) from exc

    hkl_values = [
        "; ".join(
            "(" + " ".join(str(value) for value in item.get("hkl", ())) + ")"
            for item in families
        )
        for families in pattern.hkls
    ]
    return SimulatedPattern(
        two_theta=[float(value) for value in pattern.x],
        intensity=[float(value) for value in pattern.y],
        d_spacing=[float(value) for value in pattern.d_hkls],
        hkl=hkl_values,
        radiation=resolved_radiation,
        wavelength=resolved_wavelength,
        two_theta_min=two_theta_min,
        two_theta_max=two_theta_max,
    )


def compare_trace_to_simulation(
    trace: PatternTrace,
    simulated: SimulatedPattern,
    *,
    tolerance: float = 0.2,
) -> dict[str, Any]:
    """Report peak-position diagnostics without attempting a refinement."""

    if tolerance <= 0:
        raise PatternInputError(
            "invalid_tolerance", "Peak-match tolerance must be positive."
        )
    experimental_peaks = _local_maxima(trace.two_theta, trace.intensity)
    matches: list[dict[str, Any]] = []
    used_reference: set[int] = set()
    for experimental_position, experimental_intensity in experimental_peaks:
        candidates = [
            (index, abs(experimental_position - reference_position))
            for index, reference_position in enumerate(simulated.two_theta)
            if index not in used_reference
            and abs(experimental_position - reference_position) <= tolerance
        ]
        if not candidates:
            matches.append(
                {
                    "experimental_two_theta": experimental_position,
                    "experimental_intensity": experimental_intensity,
                    "reference_two_theta": None,
                    "reference_intensity": None,
                    "difference": None,
                    "matched": False,
                }
            )
            continue
        reference_index, difference = min(candidates, key=lambda item: item[1])
        used_reference.add(reference_index)
        matches.append(
            {
                "experimental_two_theta": experimental_position,
                "experimental_intensity": experimental_intensity,
                "reference_two_theta": simulated.two_theta[reference_index],
                "reference_intensity": simulated.intensity[reference_index],
                "difference": experimental_position
                - simulated.two_theta[reference_index],
                "matched": True,
            }
        )
    unmatched_reference = [
        {
            "two_theta": position,
            "intensity": intensity,
            "d_spacing": simulated.d_spacing[index],
            "hkl": simulated.hkl[index],
        }
        for index, (position, intensity) in enumerate(
            zip(simulated.two_theta, simulated.intensity)
        )
        if index not in used_reference
        and min(trace.two_theta) <= position <= max(trace.two_theta)
    ]
    differences = [item["difference"] for item in matches if item["matched"]]
    return {
        "trace_id": trace.trace_id,
        "tolerance_degrees": tolerance,
        "matched_peak_count": len(differences),
        "experimental_peak_count": len(experimental_peaks),
        "reference_peaks_in_range": len(unmatched_reference) + len(differences),
        "mean_peak_shift_degrees": (
            sum(differences) / len(differences) if differences else None
        ),
        "matches": matches,
        "unmatched_reference_peaks": unmatched_reference,
        "chart_option": comparison_chart(
            title=f"Experimental vs ideal CIF PXRD: {trace.label}",
            experimental_x=trace.two_theta,
            experimental_y=_normalize(trace.intensity),
            reference_series=[
                reference_stick_series(
                    "Ideal CIF pattern", simulated.two_theta, simulated.intensity
                )
            ],
        ),
    }


def simulated_rows(simulated: SimulatedPattern) -> list[dict[str, Any]]:
    return [
        {
            "2Theta": two_theta,
            "NormalizedIntensity": intensity,
            "DSpacing": d_spacing,
            "HKL": hkl,
        }
        for two_theta, intensity, d_spacing, hkl in zip(
            simulated.two_theta,
            simulated.intensity,
            simulated.d_spacing,
            simulated.hkl,
        )
    ]


def _local_maxima(
    x_values: list[float], y_values: list[float]
) -> list[tuple[float, float]]:
    if len(x_values) < 3:
        return []
    maximum = max(y_values)
    threshold = maximum * 0.02
    return [
        (x_values[index], y_values[index])
        for index in range(1, len(x_values) - 1)
        if y_values[index] >= threshold
        and y_values[index] > y_values[index - 1]
        and y_values[index] >= y_values[index + 1]
    ]


def _normalize(values: list[float]) -> list[float]:
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0 for _ in values]
    return [(value - minimum) / (maximum - minimum) * 100.0 for value in values]
