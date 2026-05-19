"""Minimum interatomic distance check, split from structure_general.py."""

from __future__ import annotations

from pathlib import Path

try:
    import numpy as np

    _NP_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    _NP_AVAILABLE = False

try:
    from pymatgen.core import Molecule, Structure  # noqa: F401

    _PMG_AVAILABLE = True
except ImportError:
    _PMG_AVAILABLE = False

_IMPORT_MSG = "pymatgen not installed; install with: uv sync --extra calculation"


def check_min_interatomic_distance(
    workspace_dir: str | Path,
    *,
    filename: str,
    min_distance_A: float,
    elements: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify that all selected atom pairs are at least *min_distance_A* apart."""
    from evaluation.validators.structure_general import _load_structure, _resolve_file

    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    if not _NP_AVAILABLE:
        return False, "numpy not installed; install with: uv sync --extra calculation"

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    selected = list(range(len(struct.sites)))
    if elements:
        allowed = set(elements)
        selected = [
            idx
            for idx, site in enumerate(struct.sites)
            if getattr(site.specie, "symbol", str(site.specie)) in allowed
        ]
    if len(selected) < 2:
        scope = f" for elements {elements}" if elements else ""
        return False, f"{fpath.name}: fewer than 2 selected sites{scope}"

    min_dist = float("inf")
    min_pair: tuple[int, int] | None = None
    if isinstance(struct, Molecule):
        for pos_i, idx_i in enumerate(selected):
            for idx_j in selected[pos_i + 1 :]:
                dist = float(struct.sites[idx_i].distance(struct.sites[idx_j]))
                if dist < min_dist:
                    min_dist = dist
                    min_pair = (idx_i, idx_j)
    else:
        matrix = np.asarray(struct.distance_matrix, dtype=float)
        for pos_i, idx_i in enumerate(selected):
            for idx_j in selected[pos_i + 1 :]:
                dist = float(matrix[idx_i, idx_j])
                if dist < min_dist:
                    min_dist = dist
                    min_pair = (idx_i, idx_j)

    ok = min_dist >= min_distance_A
    pair_msg = f"pair={min_pair}" if min_pair is not None else "pair=n/a"
    return (
        ok,
        f"{fpath.name}: min interatomic distance = {min_dist:.4f} Å ({pair_msg}), "
        f"expected >= {min_distance_A} Å",
    )
