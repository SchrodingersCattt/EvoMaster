"""CSV and ECharts artifact builders for canonical PXRD service responses."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from .processing import ProcessedTrace


def csv_text(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """Return UTF-8 CSV content with a predictable column ordering."""

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def processed_rows(processed: ProcessedTrace) -> list[dict[str, Any]]:
    return [
        {
            "2Theta": two_theta,
            "Intensity": intensity,
            "NormalizedIntensity": normalized,
            "Baseline": baseline,
            "SubtractedIntensity": subtracted,
        }
        for two_theta, intensity, normalized, baseline, subtracted in zip(
            processed.trace.two_theta,
            processed.trace.intensity,
            processed.normalized_intensity,
            processed.baseline,
            processed.subtracted_intensity,
        )
    ]


def peak_rows(processed: ProcessedTrace) -> list[dict[str, Any]]:
    return [
        {
            "Trace": processed.trace.trace_id,
            "Index": peak["index"],
            "2Theta": peak["two_theta"],
            "Intensity": peak["intensity"],
            "NormalizedIntensity": peak["normalized_intensity"],
            "SubtractedIntensity": peak["subtracted_intensity"],
            "Prominence": peak["prominence"],
            "FWHM": peak["fwhm"],
            "DSpacing": peak["d_spacing"],
            "ScherrerSizeNm": peak["scherrer_size_nm"],
        }
        for peak in processed.peaks
    ]


def parse_chart(processed: ProcessedTrace, baseline_mode: str) -> dict[str, Any]:
    """Generate a portable ECharts configuration for one processed trace."""

    if baseline_mode == "Removal baseline":
        primary = processed.subtracted_intensity
        primary_name = "Baseline-subtracted intensity"
        series = [
            _line_series(primary_name, processed.trace.two_theta, primary),
            _scatter_series(processed),
        ]
    else:
        primary = processed.normalized_intensity
        series = [
            _line_series("Normalized intensity", processed.trace.two_theta, primary),
            _line_series("Baseline", processed.trace.two_theta, processed.baseline),
            _scatter_series(processed),
        ]
    return _base_chart(
        title=f"PXRD parse: {processed.trace.label}",
        x_data=processed.trace.two_theta,
        series=series,
    )


def comparison_chart(
    title: str,
    experimental_x: list[float],
    experimental_y: list[float],
    reference_series: list[dict[str, Any]],
) -> dict[str, Any]:
    series = [_line_series("Experimental", experimental_x, experimental_y)]
    series.extend(reference_series)
    return _base_chart(title=title, x_data=experimental_x, series=series)


def reference_stick_series(
    name: str, positions: list[float], intensities: list[float]
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "bar",
        "data": [
            [position, intensity] for position, intensity in zip(positions, intensities)
        ],
        "barWidth": "2%",
        "z": 1,
    }


def artifact_name(source_name: str, trace_id: str, suffix: str) -> str:
    """Create safe, stable artifact names for each source trace."""

    source = Path(source_name).stem or "pattern"
    return f"{source}_{trace_id}_{suffix}"


def _line_series(
    name: str, x_values: list[float], y_values: list[float]
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "line",
        "showSymbol": False,
        "data": [[x, y] for x, y in zip(x_values, y_values)],
        "z": 2,
    }


def _scatter_series(processed: ProcessedTrace) -> dict[str, Any]:
    return {
        "name": "Detected peaks",
        "type": "scatter",
        "symbolSize": 7,
        "data": [
            [peak["two_theta"], peak["subtracted_intensity"]]
            for peak in processed.peaks
        ],
        "z": 3,
    }


def _base_chart(
    *, title: str, x_data: list[float], series: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "title": {"text": title},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {"data": [item["name"] for item in series]},
        "toolbox": {
            "feature": {
                "dataZoom": {"yAxisIndex": "none"},
                "restore": {},
                "saveAsImage": {},
            }
        },
        "xAxis": {
            "name": "2θ (degree)",
            "type": "value",
            "min": min(x_data),
            "max": max(x_data),
        },
        "yAxis": {"name": "Intensity (a.u.)", "type": "value", "min": 0},
        "series": series,
    }
