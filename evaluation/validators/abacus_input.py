"""Validators for ABACUS INPUT file resolution checks.

Verifies that INPUT correctly resolves to the right STRU and KPT files
(whether via default names or explicit stru_file/kpoint_file parameters),
and that those resolved files have expected content.
"""

from __future__ import annotations

import re
from pathlib import Path

from .kpt_line import _parse_kpt_line
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
    kspacing_range: tuple[float, float] | None = None,
    min_kpoints: int | None = None,
) -> tuple[bool, str]:
    """Run a resolution check on an ABACUS INPUT file.

    Supported checks:
    - input_resolves_stru_lattice: verify the STRU resolved from INPUT
      has lattice vectors matching a reference file
    - input_resolves_kpt_contains: verify the KPT resolved from INPUT
      contains a required token (e.g. '4 4 4')
    - param_enabled: verify a boolean param is set to true/1
    - param_value_in: verify a param's value is in an allowed list
    - kpoint_density: verify INPUT has kspacing in range OR KPT file has
      adequate k-point mesh (each direction >= min_kpoints)
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
    elif check == "efield_dir_is_vacuum":
        return _check_efield_dir_is_vacuum(root, fpath, content, workspace_resolve)
    elif check == "kpoint_density":
        return _check_kpoint_density(
            fpath, content, kspacing_range=kspacing_range, min_kpoints=min_kpoints
        )
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
        return (
            False,
            "abacus_input_check param_enabled: 'expected' must be the param name",
        )
    pattern = re.compile(rf"(?im)^\s*{re.escape(param)}\s+(\S+)")
    match = pattern.search(content)
    if not match:
        return False, f"{fpath.name}: param '{param}' not found"
    val = match.group(1).strip().lower()
    if val in _TRUTHY:
        return True, f"{fpath.name}: {param}={match.group(1)} (enabled)"
    return (
        False,
        f"{fpath.name}: {param}={match.group(1)} (not enabled, expected true/1)",
    )


def _check_param_value_in(
    fpath: Path,
    content: str,
    expected: str | None,
    allowed: list[str] | None,
) -> tuple[bool, str]:
    """Verify that a parameter's value is one of the allowed values (case-insensitive)."""
    param = str(expected or "").strip().lower()
    if not param:
        return (
            False,
            "abacus_input_check param_value_in: 'expected' must be the param name",
        )
    if not allowed:
        return (
            False,
            "abacus_input_check param_value_in: 'allowed' list must be provided",
        )
    pattern = re.compile(rf"(?im)^\s*{re.escape(param)}\s+(\S+)")
    match = pattern.search(content)
    if not match:
        return False, f"{fpath.name}: param '{param}' not found"
    val = match.group(1).strip().lower()
    allowed_lower = [a.lower() for a in allowed]
    if val in allowed_lower:
        return True, f"{fpath.name}: {param}={match.group(1)} (in allowed: {allowed})"
    return False, (
        f"{fpath.name}: {param}={match.group(1)} " f"(not in allowed: {allowed})"
    )


def _check_efield_dir_is_vacuum(
    root: Path,
    fpath: Path,
    content: str,
    workspace_resolve: str,
) -> tuple[bool, str]:
    """Verify efield_dir points along the vacuum (longest lattice vector) direction."""
    dir_match = re.search(r"(?im)^\s*efield_dir\s+(\d+)", content)
    if not dir_match:
        return False, f"{fpath.name}: efield_dir not found"
    efield_dir = int(dir_match.group(1))
    if efield_dir not in (0, 1, 2):
        return False, f"{fpath.name}: efield_dir={efield_dir} (must be 0, 1, or 2)"

    stru_name_match = re.search(r"(?im)^\s*stru_file\s+(\S+)", content)
    stru_name = stru_name_match.group(1) if stru_name_match else "STRU"
    stru_path = _resolve_file(root, stru_name, workspace_resolve=workspace_resolve)
    if stru_path is None:
        stru_path = fpath.parent / stru_name
    if not stru_path.is_file():
        return False, f"{fpath.name}: cannot resolve STRU file '{stru_name}'"
    try:
        stru_content = stru_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {stru_path.name}: {exc}"

    vecs = _parse_lattice_vectors(stru_content)
    if vecs is None or len(vecs) < 9:
        return False, f"{stru_path.name}: LATTICE_VECTORS not found or incomplete"

    lengths = [
        (vecs[0] ** 2 + vecs[1] ** 2 + vecs[2] ** 2) ** 0.5,
        (vecs[3] ** 2 + vecs[4] ** 2 + vecs[5] ** 2) ** 0.5,
        (vecs[6] ** 2 + vecs[7] ** 2 + vecs[8] ** 2) ** 0.5,
    ]
    vacuum_dir = lengths.index(max(lengths))

    if efield_dir == vacuum_dir:
        return True, (
            f"{fpath.name}: efield_dir={efield_dir} matches vacuum direction "
            f"(lattice vector lengths: {[f'{v:.2f}' for v in lengths]})"
        )
    return False, (
        f"{fpath.name}: efield_dir={efield_dir} but vacuum direction is {vacuum_dir} "
        f"(lattice vector lengths: {[f'{v:.2f}' for v in lengths]})"
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


def _check_kpoint_density(
    fpath: Path,
    content: str,
    *,
    kspacing_range: tuple[float, float] | None = None,
    min_kpoints: int | None = None,
) -> tuple[bool, str]:
    """Verify k-point sampling is adequate via kspacing, gamma_only, OR KPT file.

    Pass if any of:
    1. INPUT contains ``kspacing`` within [lo, hi], OR
    2. INPUT contains ``gamma_only 1`` (equivalent to Gamma 1×1×1), OR
    3. A KPT file (resolved from INPUT) has a Gamma/MP mesh where each
       direction has at least ``min_kpoints`` points.
    """
    lo, hi = kspacing_range if kspacing_range else (0.04, 0.15)
    min_k = min_kpoints if min_kpoints else 4

    kspacing_match = re.search(
        r"(?im)^\s*kspacing\s+([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?(?:\s+[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)*)",
        content,
    )
    if kspacing_match:
        vals = re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", kspacing_match.group(1))
        numerics = [float(v) for v in vals]
        if all(lo <= v <= hi for v in numerics):
            return True, (
                f"{fpath.name}: kspacing={numerics} within [{lo}, {hi}]"
            )
        return False, (
            f"{fpath.name}: kspacing={numerics} outside [{lo}, {hi}]"
        )

    gamma_only_match = re.search(
        r"(?im)^\s*gamma_only\s+(1|true|\.true\.)\s*$", content
    )
    if gamma_only_match and min_k <= 1:
        return True, (
            f"{fpath.name}: gamma_only=1 (Gamma-point only, valid for min_kpoints={min_k})"
        )

    kpt_name_match = re.search(r"(?im)^\s*kpoint_file\s+(\S+)", content)
    kpt_name = kpt_name_match.group(1) if kpt_name_match else "KPT"
    kpt_path = fpath.parent / kpt_name
    if not kpt_path.is_file():
        if gamma_only_match:
            return False, (
                f"{fpath.name}: gamma_only=1 but min_kpoints={min_k} requires denser mesh"
            )
        return False, (
            f"{fpath.name}: no kspacing/gamma_only in INPUT and KPT file "
            f"'{kpt_name}' not found — no k-point sampling defined"
        )
    try:
        kpt_content = kpt_path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {kpt_path.name}: {exc}"

    mode, points = _parse_kpt_line(kpt_content)
    if mode is not None:
        # Line mode KPT — check that segment nk values are adequate
        nk_values = [p["nk"] for p in points[:-1]] if len(points) > 1 else []
        if nk_values and all(v >= min_k for v in nk_values):
            return True, (
                f"{kpt_path.name}: Line mode, segment nk={set(nk_values)} "
                f"(all >= {min_k}, resolved from {fpath.name})"
            )
        if nk_values:
            return False, (
                f"{kpt_path.name}: Line mode, segment nk={nk_values} — "
                f"some < {min_k} (resolved from {fpath.name})"
            )

    # Gamma/MP mesh mode
    mesh_match = re.search(
        r"(?:Gamma|MP|Monkhorst-Pack)\s*\n\s*(\d+)\s+(\d+)\s+(\d+)",
        kpt_content,
        re.IGNORECASE,
    )
    if not mesh_match:
        return False, (
            f"{kpt_path.name}: could not parse k-point mesh "
            f"(expected Gamma/MP line followed by N1 N2 N3, or Line mode)"
        )
    k1, k2, k3 = int(mesh_match.group(1)), int(mesh_match.group(2)), int(mesh_match.group(3))
    if k1 >= min_k and k2 >= min_k and k3 >= min_k:
        return True, (
            f"{kpt_path.name}: k-mesh {k1}×{k2}×{k3} "
            f"(all >= {min_k}, resolved from {fpath.name})"
        )
    return False, (
        f"{kpt_path.name}: k-mesh {k1}×{k2}×{k3} — "
        f"some directions < {min_k} (resolved from {fpath.name})"
    )
