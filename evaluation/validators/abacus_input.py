"""Validators for ABACUS INPUT file resolution checks.

Verifies that INPUT correctly resolves to the right STRU and KPT files
(whether via default names or explicit stru_file/kpoint_file parameters),
and that those resolved files have expected content.
"""

from __future__ import annotations

import re
from pathlib import Path

from .stru_file import _parse_lattice_vectors
from .text_file import _resolve_file


def check_abacus_input(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    expected: str | None = None,
    allowed: list[str] | None = None,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a resolution check on an ABACUS INPUT file.

    Supported checks:
    - input_resolves_stru_lattice: verify the STRU resolved from INPUT
      has lattice vectors matching a reference file
    - input_resolves_kpt_contains: verify the KPT resolved from INPUT
      contains a required token (e.g. '4 4 4')
    - param_enabled: verify a boolean param is set to true/1
    - param_value_in: verify a param's value is in an allowed list
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    if check == "input_resolves_stru_lattice":
        return _check_stru_lattice(root, fpath, content, expected, workspace_resolve)
    elif check == "input_resolves_kpt_contains":
        return _check_kpt_contains(fpath, content, expected)
    elif check == "param_enabled":
        return _check_param_enabled(fpath, content, expected)
    elif check == "param_value_in":
        return _check_param_value_in(fpath, content, expected, allowed)
    else:
        return False, f"unknown abacus_input_check check type: {check!r}"


_TRUTHY = {"true", "1", ".true.", "t", "yes"}


def _check_param_enabled(
    fpath: Path,
    content: str,
    expected: str | None,
) -> tuple[bool, str]:
    """Verify that a boolean parameter in INPUT is enabled (true/1/.true./T)."""
    param = str(expected or "").strip().lower()
    if not param:
        return False, "abacus_input_check param_enabled: 'expected' must be the param name"
    pattern = re.compile(rf"(?im)^\s*{re.escape(param)}\s+(\S+)")
    match = pattern.search(content)
    if not match:
        return False, f"{fpath.name}: param '{param}' not found"
    val = match.group(1).strip().lower()
    if val in _TRUTHY:
        return True, f"{fpath.name}: {param}={match.group(1)} (enabled)"
    return False, f"{fpath.name}: {param}={match.group(1)} (not enabled, expected true/1)"


def _check_param_value_in(
    fpath: Path,
    content: str,
    expected: str | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify that a parameter's value is one of the allowed values (case-insensitive)."""
    param = str(expected or "").strip().lower()
    if not param:
        return False, "abacus_input_check param_value_in: 'expected' must be the param name"
    if not allowed:
        return False, "abacus_input_check param_value_in: 'allowed' list must be provided"
    pattern = re.compile(rf"(?im)^\s*{re.escape(param)}\s+(\S+)")
    match = pattern.search(content)
    if not match:
        return False, f"{fpath.name}: param '{param}' not found"
    val = match.group(1).strip().lower()
    allowed_lower = [a.lower() for a in allowed]
    if val in allowed_lower:
        return True, f"{fpath.name}: {param}={match.group(1)} (in allowed: {allowed})"
    return False, (
        f"{fpath.name}: {param}={match.group(1)} "
        f"(not in allowed: {allowed})"
    )


def _check_stru_lattice(
    root: Path,
    fpath: Path,
    content: str,
    expected: str | None,
    workspace_resolve: str,
) -> tuple[bool, str]:
    """Verify INPUT resolves to a STRU whose lattice matches a reference."""
    ref_filename = str(expected or "")
    if not ref_filename:
        return (
            False,
            "abacus_input_check input_resolves_stru_lattice: 'expected' must be the reference STRU filename",
        )
    ref_path = _resolve_file(root, ref_filename, workspace_resolve=workspace_resolve)
    if ref_path is None:
        return False, f"no file matching {ref_filename!r} in {root}"
    try:
        ref_content = ref_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {ref_path.name}: {exc}"

    stru_name_match = re.search(r"(?im)^\s*stru_file\s+(\S+)", content)
    stru_name = stru_name_match.group(1) if stru_name_match else "STRU"
    input_dir = fpath.parent
    stru_path = input_dir / stru_name
    if not stru_path.is_file():
        return False, (
            f"{fpath.name}: stru_file={stru_name!r} but {stru_path} not found"
        )
    try:
        stru_content = stru_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {stru_path.name}: {exc}"

    vecs_a = _parse_lattice_vectors(stru_content)
    vecs_b = _parse_lattice_vectors(ref_content)
    if vecs_a is None:
        return False, f"{stru_path.name}: LATTICE_VECTORS not found"
    if vecs_b is None:
        return False, f"{ref_path.name}: LATTICE_VECTORS not found"
    diff = max(abs(a - b) for a, b in zip(vecs_a, vecs_b))
    if diff < 0.01:
        return True, (
            f"{stru_path.name} (resolved from {fpath.name}) matches reference "
            f"{ref_path.name} (max diff={diff:.6f} Å)"
        )
    return False, (
        f"{stru_path.name} (resolved from {fpath.name}) does NOT match reference "
        f"{ref_path.name} (max diff={diff:.4f} Å) — wrong structure selected"
    )


def _check_kpt_contains(
    fpath: Path,
    content: str,
    expected: str | None,
) -> tuple[bool, str]:
    """Verify INPUT resolves to a KPT file containing a required token."""
    token = str(expected or "")
    if not token:
        return (
            False,
            "abacus_input_check input_resolves_kpt_contains: 'expected' must be the required token (e.g. '4 4 4')",
        )

    kpt_name_match = re.search(r"(?im)^\s*kpoint_file\s+(\S+)", content)
    kpt_name = kpt_name_match.group(1) if kpt_name_match else "KPT"
    input_dir = fpath.parent
    kpt_path = input_dir / kpt_name
    if not kpt_path.is_file():
        return False, (
            f"{fpath.name}: kpoint_file={kpt_name!r} but {kpt_path} not found"
        )
    try:
        kpt_content = kpt_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {kpt_path.name}: {exc}"

    if token in kpt_content:
        return True, (
            f"{kpt_path.name} (resolved from {fpath.name}) contains {token!r}"
        )
    return False, (
        f"{kpt_path.name} (resolved from {fpath.name}) does NOT contain "
        f"{token!r} — wrong k-point mesh selected"
    )
