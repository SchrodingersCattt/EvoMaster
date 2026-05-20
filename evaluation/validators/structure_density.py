"""Density validators for periodic structure files."""

from __future__ import annotations

from pathlib import Path

from evaluation.validators.structure_general import (
    _IMPORT_MSG,
    _PMG_AVAILABLE,
    _load_structure,
    _resolve_file,
)


def check_density(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected: float | None = None,
    tolerance: float | None = None,
    min_g_cm3: float | None = None,
    max_g_cm3: float | None = None,
) -> tuple[bool, str]:
    """Verify a periodic structure density in g/cm^3."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG

    from pymatgen.core import Molecule

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if isinstance(struct, Molecule):
        return False, f"{fpath.name}: density requires a periodic Structure"

    density = float(struct.density)
    if min_g_cm3 is None or max_g_cm3 is None:
        if expected is None:
            expected = 0.0
        if tolerance is None:
            tolerance = 0.0
        min_g_cm3 = float(expected) - float(tolerance)
        max_g_cm3 = float(expected) + float(tolerance)

    hit = float(min_g_cm3) <= density <= float(max_g_cm3)
    return (
        hit,
        f"{fpath.name}: density={density:.3f} g/cm^3, "
        f"expected in [{float(min_g_cm3):.3f}, {float(max_g_cm3):.3f}]",
    )
