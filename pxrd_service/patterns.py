"""Canonical PXRD input parsing and validation.

The service accepts several text-oriented diffraction formats.  Every successful
parse is normalized into one or more :class:`PatternTrace` instances so that
preprocessing and phase matching do not depend on source-specific readers.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ElementTree
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

TEXT_PATTERN_SUFFIXES = {
    ".asc",
    ".csv",
    ".dat",
    ".raw",
    ".txt",
    ".xy",
    ".xye",
}
PARSE_SUFFIXES = TEXT_PATTERN_SUFFIXES | {".mdi", ".xrdml"}
MIN_POINTS = 15


class PatternInputError(ValueError):
    """A stable error for invalid or unsupported diffraction input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)

    @property
    def code(self) -> str:
        return self.args[0]

    @property
    def message(self) -> str:
        return self.args[1]

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class PatternTrace:
    """One measured PXRD trace in canonical units."""

    trace_id: str
    label: str
    two_theta: list[float]
    intensity: list[float]
    source_columns: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PatternDataset:
    """All traces parsed from one uploaded source file."""

    source_name: str
    source_format: str
    traces: list[PatternTrace]
    encoding: str | None = None
    delimiter: str | None = None
    warnings: list[str] = field(default_factory=list)


def parse_pattern_bytes(file_name: str, content: bytes) -> PatternDataset:
    """Parse one uploaded pattern file into validated canonical traces."""

    suffix = Path(file_name).suffix.lower()
    if suffix not in PARSE_SUFFIXES:
        raise PatternInputError(
            "unsupported_input_type",
            "Unsupported raw XRD format. Supported formats: "
            + ", ".join(sorted(PARSE_SUFFIXES)),
        )
    if suffix == ".xrdml":
        return _parse_xrdml(file_name, content)
    if suffix == ".mdi":
        return _parse_mdi(file_name, content)
    if suffix == ".raw" and b"\x00" in content:
        raise PatternInputError(
            "unsupported_binary_raw",
            "Binary .raw files are not supported. Export the pattern as XY, "
            "two-column CSV, or XRDML and upload that file instead.",
        )
    return _parse_text_dataset(file_name, content)


def select_traces(
    dataset: PatternDataset, requested: Iterable[str] | None
) -> list[PatternTrace]:
    """Return the named traces, rejecting unknown selections explicitly."""

    names = [name.strip() for name in requested or [] if name.strip()]
    if not names:
        return dataset.traces
    available = {trace.trace_id: trace for trace in dataset.traces}
    unknown = [name for name in names if name not in available]
    if unknown:
        raise PatternInputError(
            "unknown_trace",
            "Unknown trace ID(s): "
            + ", ".join(unknown)
            + ". Available trace IDs: "
            + ", ".join(available),
        )
    return [available[name] for name in names]


def _decode_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp1252", "latin-1"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise PatternInputError(
        "unsupported_encoding",
        "Could not decode the diffraction text file using UTF-8, GBK, CP1252, "
        "or Latin-1.",
    )


def _parse_text_dataset(file_name: str, content: bytes) -> PatternDataset:
    text, encoding = _decode_text(content)
    lines = _meaningful_lines(text)
    if not lines:
        raise PatternInputError(
            "no_numeric_data", "The input file contains no data rows."
        )

    delimiter = _select_delimiter(lines)
    token_rows = [(index, _split_line(line, delimiter)) for index, line in lines]
    numeric_rows = [
        (index, values)
        for index, tokens in token_rows
        if (values := _numeric_values(tokens)) is not None and len(values) >= 2
    ]
    if not numeric_rows:
        raise PatternInputError(
            "no_numeric_data",
            "No rows with at least two numeric diffraction columns were found.",
        )

    first_numeric_index = numeric_rows[0][0]
    header = _find_header(token_rows, first_numeric_index, len(numeric_rows[0][1]))
    width = Counter(len(values) for _, values in numeric_rows).most_common(1)[0][0]
    numeric_rows = [values for _, values in numeric_rows if len(values) == width]
    if len(numeric_rows) < MIN_POINTS:
        raise PatternInputError(
            "insufficient_points",
            f"At least {MIN_POINTS} numeric points are required; found {len(numeric_rows)}.",
        )

    columns = header or [f"column_{index + 1}" for index in range(width)]
    if len(columns) != width:
        columns = [f"column_{index + 1}" for index in range(width)]
    traces = _traces_from_columns(numeric_rows, columns)
    return PatternDataset(
        source_name=file_name,
        source_format=Path(file_name).suffix.lower().lstrip("."),
        traces=traces,
        encoding=encoding,
        delimiter=_delimiter_label(delimiter),
    )


def _meaningful_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!", "//")):
            continue
        if stripped.startswith(";") and ";" not in stripped[1:]:
            continue
        if "=" in stripped and not re.match(r"^[+-]?(?:\d|\.\d)", stripped):
            continue
        result.append((index, stripped))
    return result


def _select_delimiter(lines: list[tuple[int, str]]) -> str | None:
    candidates: tuple[str | None, ...] = (",", "\t", ";", None)
    scores: dict[str | None, tuple[int, int]] = {}
    for delimiter in candidates:
        numeric_count = 0
        column_total = 0
        for _, line in lines:
            values = _numeric_values(_split_line(line, delimiter))
            if values is not None and len(values) >= 2:
                numeric_count += 1
                column_total += len(values)
        scores[delimiter] = (numeric_count, column_total)
    best = max(candidates, key=lambda value: scores[value])
    if scores[best][0] == 0:
        raise PatternInputError(
            "no_numeric_data",
            "No rows with at least two numeric diffraction columns were found.",
        )
    return best


def _split_line(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        return [part for part in re.split(r"\s+", line.strip()) if part]
    return [part.strip() for part in line.split(delimiter) if part.strip()]


def _numeric_values(tokens: list[str]) -> list[float] | None:
    values: list[float] = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            return None
        if not math.isfinite(value):
            raise PatternInputError(
                "nonfinite_value", "Diffraction data contains NaN or infinite values."
            )
        values.append(value)
    return values


def _find_header(
    token_rows: list[tuple[int, list[str]]], first_numeric_index: int, width: int
) -> list[str] | None:
    for index, tokens in reversed(token_rows):
        if index >= first_numeric_index or len(tokens) != width:
            continue
        if _numeric_values(tokens) is not None:
            continue
        if any(
            _is_theta_header(token) or _is_intensity_header(token) for token in tokens
        ):
            return tokens
    return None


def _is_theta_header(value: str) -> bool:
    normalized = _normalize_header(value)
    return normalized in {
        "2theta",
        "theta",
        "twotheta",
        "angle",
        "position",
        "positiondeg",
    } or normalized.startswith(("2theta", "theta", "twotheta"))


def _is_intensity_header(value: str) -> bool:
    normalized = _normalize_header(value)
    return normalized in {
        "intensity",
        "counts",
        "count",
        "cps",
        "signal",
    } or normalized.startswith(("intensity", "counts", "count", "cps"))


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().replace("θ", "theta"))


def _traces_from_columns(
    rows: list[list[float]], columns: list[str]
) -> list[PatternTrace]:
    width = len(columns)
    theta_columns = [
        index for index, column in enumerate(columns) if _is_theta_header(column)
    ]
    trace_specs: list[tuple[int, int, str]] = []

    if len(theta_columns) == 1:
        theta_index = theta_columns[0]
        intensity_columns = [
            index
            for index, column in enumerate(columns)
            if index != theta_index and (_is_intensity_header(column) or width == 2)
        ]
        if not intensity_columns:
            intensity_columns = [
                index for index in range(width) if index != theta_index
            ]
        trace_specs = [
            (theta_index, intensity_index, columns[intensity_index])
            for intensity_index in intensity_columns
        ]
    elif len(theta_columns) > 1:
        for theta_index in theta_columns:
            intensity_index = theta_index + 1
            if intensity_index < width:
                trace_specs.append(
                    (theta_index, intensity_index, columns[intensity_index])
                )
    elif width == 2:
        trace_specs = [(0, 1, "trace_1")]
    elif width % 2 == 0:
        trace_specs = [
            (index, index + 1, f"trace_{index // 2 + 1}")
            for index in range(0, width, 2)
        ]
    else:
        trace_specs = [(0, index, columns[index]) for index in range(1, width)]

    if not trace_specs:
        raise PatternInputError(
            "invalid_columns",
            "Could not infer 2Theta and intensity columns from the input table.",
        )

    traces: list[PatternTrace] = []
    used_ids: set[str] = set()
    for number, (theta_index, intensity_index, label) in enumerate(
        trace_specs, start=1
    ):
        trace_id = _unique_trace_id(_slug(label) or f"trace_{number}", used_ids)
        two_theta = [row[theta_index] for row in rows]
        intensity = [row[intensity_index] for row in rows]
        traces.append(
            _validated_trace(
                trace_id=trace_id,
                label=label or trace_id,
                two_theta=two_theta,
                intensity=intensity,
                source_columns=[columns[theta_index], columns[intensity_index]],
            )
        )
    return traces


def _validated_trace(
    trace_id: str,
    label: str,
    two_theta: list[float],
    intensity: list[float],
    source_columns: list[str],
) -> PatternTrace:
    if len(two_theta) != len(intensity) or len(two_theta) < MIN_POINTS:
        raise PatternInputError(
            "insufficient_points",
            f"Trace {trace_id!r} requires at least {MIN_POINTS} paired points.",
        )
    if any(not math.isfinite(value) for value in (*two_theta, *intensity)):
        raise PatternInputError(
            "nonfinite_value", f"Trace {trace_id!r} contains NaN or infinite values."
        )

    warnings: list[str] = []
    ordered = sorted(zip(two_theta, intensity), key=lambda pair: pair[0])
    if ordered != list(zip(two_theta, intensity)):
        warnings.append("sorted_by_two_theta")

    deduplicated: list[tuple[float, float]] = []
    position = 0
    while position < len(ordered):
        theta = ordered[position][0]
        values: list[float] = []
        while position < len(ordered) and ordered[position][0] == theta:
            values.append(ordered[position][1])
            position += 1
        if len(values) > 1:
            warnings.append("duplicate_two_theta_averaged")
        deduplicated.append((theta, sum(values) / len(values)))

    clean_theta = [pair[0] for pair in deduplicated]
    clean_intensity = [pair[1] for pair in deduplicated]
    if len(clean_theta) < MIN_POINTS:
        raise PatternInputError(
            "insufficient_points",
            f"Trace {trace_id!r} has fewer than {MIN_POINTS} points after de-duplication.",
        )
    if clean_theta[0] <= 0:
        warnings.append("nonpositive_two_theta")
    if max(clean_intensity) <= min(clean_intensity):
        raise PatternInputError(
            "flat_intensity", f"Trace {trace_id!r} has no intensity variation."
        )
    return PatternTrace(
        trace_id=trace_id,
        label=label,
        two_theta=clean_theta,
        intensity=clean_intensity,
        source_columns=source_columns,
        warnings=list(dict.fromkeys(warnings)),
    )


def _parse_xrdml(file_name: str, content: bytes) -> PatternDataset:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise PatternInputError(
            "invalid_xrdml", "The XRDML XML document is invalid."
        ) from exc

    intensity_nodes = [
        node
        for node in root.iter()
        if _local_name(node.tag) in {"intensities", "counts"}
        and (node.text or "").strip()
    ]
    traces: list[PatternTrace] = []
    for number, node in enumerate(intensity_nodes, start=1):
        values = _float_tokens(node.text or "")
        if len(values) < MIN_POINTS:
            continue
        parent = _find_parent(root, node)
        start, end = _scan_range(parent or root)
        if start is None or end is None or start == end:
            continue
        step = (end - start) / (len(values) - 1)
        theta = [start + index * step for index in range(len(values))]
        traces.append(
            _validated_trace(
                trace_id=f"trace_{number}",
                label=f"scan_{number}",
                two_theta=theta,
                intensity=values,
                source_columns=["2Theta", "Intensity"],
            )
        )
    if not traces:
        raise PatternInputError(
            "invalid_xrdml",
            "Could not find an XRDML scan with intensities and a 2Theta range.",
        )
    return PatternDataset(
        source_name=file_name,
        source_format="xrdml",
        traces=traces,
        encoding="utf-8",
        delimiter=None,
    )


def _parse_mdi(file_name: str, content: bytes) -> PatternDataset:
    text, encoding = _decode_text(content)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header: tuple[float, float, int, int] | None = None
    for index, line in enumerate(lines):
        values = _float_tokens(line)
        if len(values) < 4:
            continue
        start, step, _, count = values[:4]
        if 0 <= start <= 180 and 0 < step <= 1 and MIN_POINTS <= count <= 200000:
            header = (start, step, int(round(count)), index)
            break
    if header is None:
        raise PatternInputError(
            "invalid_mdi", "Could not locate valid MDI start/step/count metadata."
        )
    start, step, count, header_index = header
    intensity = [
        value for line in lines[header_index + 1 :] for value in _float_tokens(line)
    ][:count]
    if len(intensity) < MIN_POINTS:
        raise PatternInputError(
            "insufficient_points", "MDI input contains too few intensity points."
        )
    theta = [start + index * step for index in range(len(intensity))]
    trace = _validated_trace(
        trace_id="trace_1",
        label="mdi_scan",
        two_theta=theta,
        intensity=intensity,
        source_columns=["2Theta", "Intensity"],
    )
    return PatternDataset(
        source_name=file_name,
        source_format="mdi",
        traces=[trace],
        encoding=encoding,
    )


def _float_tokens(value: str) -> list[float]:
    result: list[float] = []
    for token in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value):
        parsed = float(token)
        if math.isfinite(parsed):
            result.append(parsed)
    return result


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1].lower()


def _find_parent(
    root: ElementTree.Element, child: ElementTree.Element
) -> ElementTree.Element | None:
    for parent in root.iter():
        if child in list(parent):
            return parent
    return None


def _scan_range(node: ElementTree.Element) -> tuple[float | None, float | None]:
    start: float | None = None
    end: float | None = None
    for child in node.iter():
        name = _local_name(child.tag)
        values = _float_tokens(child.text or "")
        if not values:
            continue
        if name == "startposition":
            start = values[0]
        elif name == "endposition":
            end = values[0]
    return start, end


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _unique_trace_id(base: str, used: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _delimiter_label(delimiter: str | None) -> str:
    if delimiter is None:
        return "whitespace"
    return {"\t": "tab", ",": "comma", ";": "semicolon"}[delimiter]
