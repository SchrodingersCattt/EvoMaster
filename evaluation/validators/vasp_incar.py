"""Validators for VASP INCAR files.

Handles VASP-specific syntax: MAGMOM repeat notation (N*val), boolean tags
(.TRUE./.FALSE.), and array parameters (LDAUL, LDAUU, LDAUJ).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .text_file import _resolve_file

_VASP_TRUE = {".true.", "t", ".t.", "true"}
_VASP_FALSE = {".false.", "f", ".f.", "false"}


def _parse_incar(content: str) -> dict[str, str]:
    """Parse INCAR into {TAG: raw_value_string} dict (case-insensitive keys)."""
    params: dict[str, str] = {}
    for line in content.splitlines():
        line = line.split("#")[0].split("!")[0].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().upper()
        val = val.strip().rstrip(";")
        if key:
            params[key] = val
    return params


def _expand_magmom(raw: str) -> list[float]:
    """Expand VASP MAGMOM notation: '2*2.5 3*0.0' → [2.5, 2.5, 0.0, 0.0, 0.0]."""
    tokens = raw.split()
    result: list[float] = []
    for tok in tokens:
        if "*" in tok:
            parts = tok.split("*")
            count = int(parts[0])
            value = float(parts[1])
            result.extend([value] * count)
        else:
            try:
                result.append(float(tok))
            except ValueError:
                continue
    return result


def check_vasp_incar(
    workspace_dir: str | Path,
    *,
    filename: str,
    check: str,
    param: str | None = None,
    expected: Any = None,
    min: float | None = None,
    max: float | None = None,
    atom_count: int | None = None,
    species_index: int | None = None,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Run a semantic check on a VASP INCAR file.

    Supported checks:
    - param_set: verify param exists in INCAR
    - param_value: verify param equals expected (handles .TRUE./.FALSE.)
    - param_range: verify numeric param within [min, max]
    - magmom_per_atom_range: expand MAGMOM, check each atom's value in [min, max]
    - magmom_atom_count: expand MAGMOM, check total atom count matches
    - magmom_total_range: expand MAGMOM, check sum of moments in [min, max]
    - array_value: verify a specific element in an array param (e.g., LDAUL[0]=2);
      uses species_index (0-based)
    """
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"failed reading {fpath.name}: {exc}"

    params = _parse_incar(content)
    tag = (param or "").upper()

    if check == "param_set":
        if tag in params:
            return True, f"{fpath.name}: {tag} is set (={params[tag]})"
        return False, f"{fpath.name}: {tag} not found"

    elif check == "param_value":
        if tag not in params:
            return False, f"{fpath.name}: {tag} not found"
        actual = params[tag].strip().lower()
        exp = str(expected).strip().lower()
        if exp in _VASP_TRUE:
            ok = actual in _VASP_TRUE
        elif exp in _VASP_FALSE:
            ok = actual in _VASP_FALSE
        else:
            ok = actual == exp
        if ok:
            return True, f"{fpath.name}: {tag}={params[tag]} matches expected"
        return False, f"{fpath.name}: {tag}={params[tag]}, expected {expected}"

    elif check == "param_range":
        if tag not in params:
            return False, f"{fpath.name}: {tag} not found"
        try:
            val = float(re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", params[tag])[0])
        except (IndexError, ValueError):
            return False, f"{fpath.name}: {tag}={params[tag]}, cannot parse numeric"
        lo = min if min is not None else float("-inf")
        hi = max if max is not None else float("inf")
        if lo <= val <= hi:
            return True, f"{fpath.name}: {tag}={val}, within [{lo}, {hi}]"
        return False, f"{fpath.name}: {tag}={val}, outside [{lo}, {hi}]"

    elif check == "magmom_per_atom_range":
        if "MAGMOM" not in params:
            return False, f"{fpath.name}: MAGMOM not found"
        moments = _expand_magmom(params["MAGMOM"])
        if not moments:
            return False, f"{fpath.name}: MAGMOM is empty or unparseable"
        lo = min if min is not None else float("-inf")
        hi = max if max is not None else float("inf")
        bad = [(i, v) for i, v in enumerate(moments) if not (lo <= v <= hi)]
        if bad:
            return False, (
                f"{fpath.name}: MAGMOM atoms out of range [{lo}, {hi}]: "
                f"{bad[:5]}"
            )
        return True, (
            f"{fpath.name}: MAGMOM all {len(moments)} atoms in [{lo}, {hi}]"
        )

    elif check == "magmom_atom_count":
        if "MAGMOM" not in params:
            return False, f"{fpath.name}: MAGMOM not found"
        moments = _expand_magmom(params["MAGMOM"])
        if atom_count is not None and len(moments) != atom_count:
            return False, (
                f"{fpath.name}: MAGMOM has {len(moments)} atoms, expected {atom_count}"
            )
        return True, f"{fpath.name}: MAGMOM has {len(moments)} atoms"

    elif check == "magmom_total_range":
        if "MAGMOM" not in params:
            return False, f"{fpath.name}: MAGMOM not found"
        moments = _expand_magmom(params["MAGMOM"])
        total = sum(moments)
        lo = min if min is not None else float("-inf")
        hi = max if max is not None else float("inf")
        if lo <= total <= hi:
            return True, f"{fpath.name}: MAGMOM total={total}, within [{lo}, {hi}]"
        return False, f"{fpath.name}: MAGMOM total={total}, outside [{lo}, {hi}]"

    elif check == "array_value":
        if tag not in params:
            return False, f"{fpath.name}: {tag} not found"
        raw = params[tag]
        tokens = raw.split()
        idx = species_index if species_index is not None else 0
        if idx >= len(tokens):
            return False, (
                f"{fpath.name}: {tag} has {len(tokens)} elements, "
                f"index {idx} out of range"
            )
        actual_str = tokens[idx]
        try:
            actual_val = float(actual_str)
        except ValueError:
            actual_val = None
        if expected is not None:
            exp_val = float(expected)
            if actual_val is not None and actual_val == exp_val:
                return True, f"{fpath.name}: {tag}[{idx}]={actual_val}"
            return False, (
                f"{fpath.name}: {tag}[{idx}]={actual_str}, expected {expected}"
            )
        if min is not None or max is not None:
            if actual_val is None:
                return False, f"{fpath.name}: {tag}[{idx}]={actual_str}, not numeric"
            lo = min if min is not None else float("-inf")
            hi = max if max is not None else float("inf")
            if lo <= actual_val <= hi:
                return True, f"{fpath.name}: {tag}[{idx}]={actual_val}, in [{lo}, {hi}]"
            return False, (
                f"{fpath.name}: {tag}[{idx}]={actual_val}, outside [{lo}, {hi}]"
            )
        return True, f"{fpath.name}: {tag}[{idx}]={actual_str}"

    else:
        return False, f"unknown vasp_incar_check check type: {check!r}"
