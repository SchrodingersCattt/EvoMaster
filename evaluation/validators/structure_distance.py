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


def check_bond_range(
    workspace_dir: str | Path,
    *,
    filename: str,
    element_a: str,
    element_b: str,
    min_distance: float = 0.0,
    max_distance: float = 5.0,
    n_neighbors: int = 0,
) -> tuple[bool, str]:
    """Verify nearest-neighbor distances for an element pair are within range."""
    from evaluation.validators.structure_general import _load_structure, _resolve_file

    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    if not _NP_AVAILABLE:
        return False, "numpy not installed"
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    sites = struct.sites
    a_indices = [i for i, s in enumerate(sites) if s.species_string == element_a]
    b_indices = [i for i, s in enumerate(sites) if s.species_string == element_b]
    if not a_indices:
        return False, f"{fpath.name}: element {element_a!r} not found"
    if not b_indices:
        return False, f"{fpath.name}: element {element_b!r} not found"

    violations = []
    all_nn_dists: list[float] = []
    for ai in a_indices:
        dists = []
        for bi in b_indices:
            if ai == bi:
                continue
            d = struct.get_distance(ai, bi)
            dists.append(d)
        dists.sort()
        nn = (
            dists[:n_neighbors]
            if n_neighbors > 0
            else [d for d in dists if d <= max_distance * 1.5]
        )
        all_nn_dists.extend(nn)
        for d in nn:
            if d < min_distance or d > max_distance:
                violations.append(d)

    if not all_nn_dists:
        return False, f"{fpath.name}: no {element_a}-{element_b} distances found"
    mean_d = float(np.mean(all_nn_dists))
    if violations:
        return False, (
            f"{fpath.name}: {len(violations)} {element_a}-{element_b} distances "
            f"outside [{min_distance}, {max_distance}] Å "
            f"(mean={mean_d:.3f} Å, worst={min(violations):.3f}/{max(violations):.3f} Å)"
        )
    return True, (
        f"{fpath.name}: all {len(all_nn_dists)} {element_a}-{element_b} nearest-neighbor "
        f"distances in [{min_distance}, {max_distance}] Å (mean={mean_d:.3f} Å)"
    )
