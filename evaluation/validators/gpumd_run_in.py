"""Validators for GPUMD run.in input files.

GPUMD run.in is line-oriented: `<keyword> [params...]`, comments start with `#`,
order matters, multiple blocks possible (equilibration + production).
"""

from __future__ import annotations

from pathlib import Path

from .text_file import _resolve_file


def _parse_lines(content: str) -> list[list[str]]:
    """Parse run.in into tokenized non-comment, non-empty lines."""
    result: list[list[str]] = []
    for raw_line in content.splitlines():
        stripped = raw_line.split("#", 1)[0].strip()
        if not stripped:
            continue
        tokens = stripped.split()
        if tokens:
            result.append(tokens)
    return result


def check_gpumd_run_in(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: str | list[str] | None = None,
    allowed: list[str] | None = None,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a semantic check on a GPUMD run.in file.

    Supported checks:
    - ensemble_type: verify ensemble uses one of the allowed types
    - has_keyword: verify keywords appear in non-comment lines
    - keyword_before: verify command ordering (expected=[before, after])
    - param_count: verify a keyword has allowed parameter counts
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    lines = _parse_lines(content)

    if check == "ensemble_type":
        return _check_ensemble_type(fpath, lines, allowed)
    elif check == "has_keyword":
        return _check_has_keyword(fpath, lines, expected)
    elif check == "has_any_keyword_set":
        return _check_has_any_keyword_set(fpath, lines, allowed)
    elif check == "keyword_before":
        return _check_keyword_before(fpath, lines, expected)
    elif check == "first_keyword":
        return _check_first_keyword(fpath, lines, expected)
    elif check == "min_keyword_count":
        return _check_min_keyword_count(fpath, lines, expected, allowed)
    elif check == "param_count":
        return _check_param_count(fpath, lines, expected, allowed)
    else:
        return False, f"unknown gpumd_run_in_check check type: {check!r}"


def _check_ensemble_type(
    fpath: Path,
    lines: list[list[str]],
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify ensemble keyword uses one of the allowed types."""
    if not allowed:
        return False, "gpumd_run_in_check ensemble_type: 'allowed' list must be provided"

    ensemble_lines = [line for line in lines if line[0].lower() == "ensemble"]
    if not ensemble_lines:
        return False, f"{fpath.name}: no 'ensemble' keyword found"

    allowed_lower = [a.lower() for a in allowed]

    for line in ensemble_lines:
        if len(line) < 2:
            continue
        ens_type = line[1].lower()
        if ens_type in allowed_lower:
            return True, (
                f"{fpath.name}: ensemble type '{line[1]}' is in allowed: {allowed}"
            )

    found_types = [line[1] if len(line) >= 2 else "(no type)" for line in ensemble_lines]
    return False, (
        f"{fpath.name}: ensemble type(s) {found_types} not in allowed: {allowed}"
    )


def _check_has_keyword(
    fpath: Path,
    lines: list[list[str]],
    expected: str | list[str] | None,
) -> tuple[bool, str]:
    """Verify specified keywords appear as commands (first token) in non-comment lines."""
    if not expected:
        return False, "gpumd_run_in_check has_keyword: 'expected' must be provided"

    keywords = [expected] if isinstance(expected, str) else list(expected)
    present_keywords = {line[0].lower() for line in lines}

    missing = [kw for kw in keywords if kw.lower() not in present_keywords]
    if not missing:
        return True, f"{fpath.name}: all keywords found: {keywords}"
    return False, f"{fpath.name}: missing keywords: {missing}"


def _check_has_any_keyword_set(
    fpath: Path,
    lines: list[list[str]],
    allowed: list[str] | list[list[str]] | None,
) -> tuple[bool, str]:
    """Pass if at least one keyword set is fully present (OR between sets).

    allowed is a list of keyword sets, e.g.:
      [["compute_phonon", "replicate"], ["compute_dos"]]
    means: pass if (compute_phonon AND replicate) OR (compute_dos) are present.

    If allowed is a flat list of strings, each string is treated as a single-keyword set.
    """
    if not allowed:
        return False, "gpumd_run_in_check has_any_keyword_set: 'allowed' list required"

    present_keywords = {line[0].lower() for line in lines}

    sets: list[list[str]] = []
    for item in allowed:
        if isinstance(item, list):
            sets.append(item)
        else:
            sets.append([str(item)])

    for kw_set in sets:
        missing = [kw for kw in kw_set if kw.lower() not in present_keywords]
        if not missing:
            return True, (
                f"{fpath.name}: keyword set {kw_set} fully present"
            )

    return False, (
        f"{fpath.name}: none of the allowed keyword sets found: {sets} "
        f"(present commands: {sorted(present_keywords)})"
    )


def _check_first_keyword(
    fpath: Path,
    lines: list[list[str]],
    expected: str | list[str] | None,
) -> tuple[bool, str]:
    """Verify that the first non-comment command is the expected keyword."""
    if not expected:
        return False, "gpumd_run_in_check first_keyword: 'expected' must be provided"

    keyword = expected if isinstance(expected, str) else expected[0]

    if not lines:
        return False, f"{fpath.name}: file has no commands"

    first_cmd = lines[0][0].lower()
    if first_cmd == keyword.lower():
        return True, f"{fpath.name}: first command is '{lines[0][0]}'"
    return False, (
        f"{fpath.name}: first command is '{lines[0][0]}', expected '{keyword}'"
    )


def _check_min_keyword_count(
    fpath: Path,
    lines: list[list[str]],
    expected: str | list[str] | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify a keyword appears at least N times.

    expected: keyword name (str)
    allowed: ["<min_count>"] — minimum required occurrences
    """
    keyword = expected if isinstance(expected, str) else None
    if not keyword:
        return False, "gpumd_run_in_check min_keyword_count: 'expected' must be keyword name"
    if not allowed or len(allowed) < 1:
        return False, "gpumd_run_in_check min_keyword_count: 'allowed' must be ['<min_count>']"

    try:
        min_count = int(allowed[0])
    except (TypeError, ValueError):
        return False, f"gpumd_run_in_check min_keyword_count: invalid count '{allowed[0]}'"

    keyword_lower = keyword.lower()
    count = sum(1 for line in lines if line[0].lower() == keyword_lower)

    if count >= min_count:
        return True, (
            f"{fpath.name}: '{keyword}' appears {count} time(s) (required >= {min_count})"
        )
    return False, (
        f"{fpath.name}: '{keyword}' appears {count} time(s), required >= {min_count}"
    )


def _check_keyword_before(
    fpath: Path,
    lines: list[list[str]],
    expected: str | list[str] | None,
) -> tuple[bool, str]:
    """Verify ordering: expected=[before_keyword, after_keyword]."""
    if not expected or not isinstance(expected, list) or len(expected) != 2:
        return False, (
            "gpumd_run_in_check keyword_before: 'expected' must be [before, after]"
        )
    before_kw = expected[0].lower()
    after_kw = expected[1].lower()

    before_idx = None
    after_idx = None

    for i, line in enumerate(lines):
        kw = line[0].lower()
        if kw == before_kw and before_idx is None:
            before_idx = i
        if kw == after_kw and after_idx is None:
            after_idx = i

    if before_idx is None:
        return False, f"{fpath.name}: keyword '{expected[0]}' not found"
    if after_idx is None:
        return False, f"{fpath.name}: keyword '{expected[1]}' not found"

    if before_idx < after_idx:
        return True, (
            f"{fpath.name}: '{expected[0]}' (line {before_idx}) "
            f"appears before '{expected[1]}' (line {after_idx})"
        )
    return False, (
        f"{fpath.name}: '{expected[0]}' (line {before_idx}) does NOT appear "
        f"before '{expected[1]}' (line {after_idx})"
    )


def _check_param_count(
    fpath: Path,
    lines: list[list[str]],
    expected: str | list[str] | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify a keyword has one of the allowed parameter counts.

    expected: keyword name (str)
    allowed: list of allowed counts as strings (e.g., ["6", "10", "16"])
    """
    keyword = expected if isinstance(expected, str) else None
    if not keyword:
        return False, "gpumd_run_in_check param_count: 'expected' must be keyword name"
    if not allowed:
        return False, "gpumd_run_in_check param_count: 'allowed' list of counts required"

    allowed_counts: list[int] = []
    for a in allowed:
        try:
            allowed_counts.append(int(a))
        except (TypeError, ValueError):
            return False, f"gpumd_run_in_check param_count: invalid count '{a}'"

    keyword_lower = keyword.lower()
    matching_lines = [line for line in lines if line[0].lower() == keyword_lower]
    if not matching_lines:
        return False, f"{fpath.name}: keyword '{keyword}' not found"

    line = matching_lines[0]
    count = len(line) - 1

    if count in allowed_counts:
        return True, (
            f"{fpath.name}: '{keyword}' has {count} params (allowed: {allowed_counts})"
        )
    return False, (
        f"{fpath.name}: '{keyword}' has {count} params, "
        f"not in allowed: {allowed_counts}"
    )
