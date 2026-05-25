"""Validator for ABACUS KPT Line-mode files."""

from __future__ import annotations

import re
from pathlib import Path

from .text_file import _resolve_file


def _parse_kpt_line(content: str) -> tuple[str | None, list[dict]]:
    """Parse an ABACUS KPT file in Line mode.

    Returns (mode, points) where each point is:
        {"coords": (kx, ky, kz), "nk": int, "label": str | None}

    Returns (None, []) if not parseable as Line mode.
    """
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    if len(lines) < 3:
        return None, []

    if not re.match(r"(?i)K_POINTS", lines[0]):
        return None, []

    try:
        num_points = int(lines[1])
    except ValueError:
        return None, []

    mode = lines[2]
    if not re.match(r"(?i)line", mode):
        return None, []

    points: list[dict] = []
    for line in lines[3:]:
        comment = None
        if "//" in line:
            line, comment = line.split("//", 1)
            comment = comment.strip()
        elif "#" in line:
            line, comment = line.split("#", 1)
            comment = comment.strip()

        tokens = line.split()
        if len(tokens) < 4:
            continue
        try:
            kx, ky, kz = float(tokens[0]), float(tokens[1]), float(tokens[2])
            nk = int(tokens[3])
        except (ValueError, IndexError):
            continue
        label = comment or (tokens[4] if len(tokens) > 4 else None)
        points.append({"coords": (kx, ky, kz), "nk": nk, "label": label})

    return mode, points


def check_kpt_line(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: int | str | list | None = None,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a structural check on an ABACUS KPT Line-mode file.

    Supported checks:
    - mode: confirm file is Line mode
    - segment_count: number of high-symmetry points == expected (int)
    - last_nk: nk value of last point == expected (int)
    - no_nk_zero: no point has nk=0
    - nk_per_segment: all non-last points have nk within [min, max]
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    mode, points = _parse_kpt_line(content)

    if check == "mode":
        if mode is None:
            return False, f"{fpath.name}: not a valid KPT Line-mode file"
        return True, f"{fpath.name}: KPT mode=Line ({len(points)} points)"

    if mode is None:
        return False, f"{fpath.name}: not a valid KPT Line-mode file (cannot run check '{check}')"

    if check == "segment_count":
        exp = int(expected or 0)
        actual = len(points)
        if actual == exp:
            return True, f"{fpath.name}: segment_count={actual}"
        return False, f"{fpath.name}: segment_count={actual}, expected {exp}"

    elif check == "last_nk":
        if not points:
            return False, f"{fpath.name}: no k-points found"
        exp = int(expected or 1)
        actual = points[-1]["nk"]
        if actual == exp:
            return True, f"{fpath.name}: last point nk={actual}"
        return False, (
            f"{fpath.name}: last point nk={actual}, expected {exp}"
        )

    elif check == "no_nk_zero":
        zeros = [i for i, p in enumerate(points) if p["nk"] == 0]
        if not zeros:
            return True, f"{fpath.name}: no nk=0 found ({len(points)} points)"
        return False, (
            f"{fpath.name}: nk=0 found at point index(es) {zeros}"
        )

    elif check == "nk_per_segment":
        if not points:
            return False, f"{fpath.name}: no k-points found"
        cfg = expected if isinstance(expected, dict) else {}
        lo = int(cfg.get("min", 1))
        hi = int(cfg.get("max", 1000))
        segment_points = points[:-1] if len(points) > 1 else points
        nk_values = [p["nk"] for p in segment_points]
        bad = [v for v in nk_values if not (lo <= v <= hi)]
        if not bad:
            return True, (
                f"{fpath.name}: all segment nk values {set(nk_values)} "
                f"within [{lo}, {hi}]"
            )
        return False, (
            f"{fpath.name}: segment nk values {bad} outside [{lo}, {hi}]"
        )

    else:
        return False, f"unknown kpt_line_check check type: {check!r}"
