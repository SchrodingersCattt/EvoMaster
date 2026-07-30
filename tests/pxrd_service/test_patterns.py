from __future__ import annotations

import pytest

from pxrd_service.patterns import PatternInputError, parse_pattern_bytes


def test_text_parser_accepts_comments_gbk_and_shared_angle_columns() -> None:
    content = "# instrument export\n2Theta;室温;高温\n" + "\n".join(
        f"{10 + index * 0.1:.1f};{index + 1};{21 - index}" for index in range(20)
    )
    dataset = parse_pattern_bytes("series.txt", content.encode("gbk"))

    assert dataset.encoding == "gbk"
    assert dataset.delimiter == "semicolon"
    assert [trace.trace_id for trace in dataset.traces] == ["trace_1", "trace_2"]
    assert all(len(trace.two_theta) == 20 for trace in dataset.traces)


def test_text_parser_rejects_short_pattern() -> None:
    with pytest.raises(PatternInputError) as exc_info:
        parse_pattern_bytes("short.xy", b"10 1\n10.1 2\n")

    assert exc_info.value.code == "insufficient_points"


def test_text_parser_sorts_and_deduplicates_theta() -> None:
    rows = ["2Theta Intensity"] + [
        f"{10 + index * 0.1:.1f} {index + 1}" for index in range(20)
    ]
    rows.extend(["10.5 100", "10.4 50"])
    dataset = parse_pattern_bytes("unsorted.xy", "\n".join(rows).encode())
    trace = dataset.traces[0]

    assert trace.two_theta == sorted(trace.two_theta)
    assert "sorted_by_two_theta" in trace.warnings
    assert "duplicate_two_theta_averaged" in trace.warnings


def test_exclude_only_filter_is_applied() -> None:
    from pxrd_service.vendor.xrd_core.parse import PTF

    records = [
        ["carbon", "CO2"],
        ["oxide", "Fe2O3"],
    ]

    assert PTF([False, [], [], ["C"]], records) == [["oxide", "Fe2O3"]]
