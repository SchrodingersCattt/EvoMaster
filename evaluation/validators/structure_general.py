"""Pymatgen-backed programmatic checks for structure-construction evaluation items.

Requires optional dependency group ``calculation`` (includes ``pymatgen``).
All public functions follow the ``(bool, str)`` return convention used by the
MATTER evaluator.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy optional-dep imports (numpy, pymatgen)
# ---------------------------------------------------------------------------

_NP_AVAILABLE = False
try:
    import numpy as np

    _NP_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]

_PMG_AVAILABLE = False
try:
    from pymatgen.core import Molecule, Structure

    _PMG_AVAILABLE = True
except ImportError:
    pass

_IMPORT_MSG = 'pymatgen not installed; install with: uv sync --extra calculation'


# ---------------------------------------------------------------------------
# File resolution helpers
# ---------------------------------------------------------------------------


def _resolve_file(workspace: Path, pattern: str) -> Path | None:
    """Return the best-matching file inside *workspace* for *pattern*.

    Resolution order:
    1. Exact filename match (case-sensitive).
    2. ``fnmatch`` glob expansion – newest file wins.
    """
    exact = workspace / pattern
    if exact.is_file():
        return exact

    hits = [
        p
        for p in workspace.iterdir()
        if p.is_file() and fnmatch.fnmatch(p.name, pattern)
    ]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _load_structure(path: Path) -> Structure | Molecule:
    """Read a CIF / POSCAR / XYZ / … file via pymatgen auto-detection."""
    suffix = path.suffix.lower()
    if suffix in {'.xyz'}:
        return Molecule.from_file(str(path))
    return Structure.from_file(str(path))


# ---------------------------------------------------------------------------
# 1. Atom count
# ---------------------------------------------------------------------------


def check_atom_count(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected: int,
    tolerance: float = 0,
) -> tuple[bool, str]:
    """Verify the total number of atoms in a structure file."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'
    actual = len(struct)
    hit = abs(actual - expected) <= tolerance
    return hit, f'{fpath.name}: atom_count={actual}, expected={expected}±{tolerance}'


# ---------------------------------------------------------------------------
# 2. Formula
# ---------------------------------------------------------------------------


def check_formula(
    workspace_dir: str | Path,
    *,
    filename: str,
    formula: str,
) -> tuple[bool, str]:
    """Verify the reduced chemical formula of a structure file."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'
    comp = struct.composition
    # Compare both reduced and alphabetical formula representations
    actual_reduced = comp.reduced_formula
    actual_alpha = comp.alphabetical_formula
    actual_hill = comp.hill_formula

    from pymatgen.core import Composition

    try:
        expected_comp = Composition(formula)
    except Exception:
        return False, f'could not parse expected formula {formula!r}'

    # Normalise: compare element ratios
    if comp.reduced_composition == expected_comp.reduced_composition:
        return (
            True,
            f'{fpath.name}: formula={actual_reduced} matches expected {formula}',
        )
    return (
        False,
        f'{fpath.name}: formula={actual_reduced} (hill={actual_hill}, alpha={actual_alpha}) '
        f'does not match expected {formula}',
    )


# ---------------------------------------------------------------------------
# 3. Bond count (number of bonds between element pair shorter than cutoff)
# ---------------------------------------------------------------------------


def check_bond_count(
    workspace_dir: str | Path,
    *,
    filename: str,
    element_a: str,
    element_b: str,
    cutoff_A: float,
    expected_count: int,
    tolerance: float = 0,
) -> tuple[bool, str]:
    """Count bonds between *element_a* and *element_b* shorter than *cutoff_A*."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'

    count = 0
    sites = struct.sites
    for i, si in enumerate(sites):
        if si.species_string != element_a:
            continue
        for j, sj in enumerate(sites):
            if j <= i and element_a == element_b:
                continue
            if j == i:
                continue
            if sj.species_string != element_b:
                continue
            if isinstance(struct, Molecule):
                dist = si.distance(sj)
            else:
                dist = struct.get_distance(i, j)
            if dist < cutoff_A:
                count += 1

    hit = abs(count - expected_count) <= tolerance
    return (
        hit,
        f'{fpath.name}: {element_a}-{element_b} bonds (<{cutoff_A} Å) = {count}, '
        f'expected={expected_count}±{tolerance}',
    )


# ---------------------------------------------------------------------------
# 4. Representative bond length
# ---------------------------------------------------------------------------


def check_bond_length(
    workspace_dir: str | Path,
    *,
    filename: str,
    element_a: str,
    element_b: str,
    cutoff_A: float = 3.0,
    expected: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Check that the mean bond length between *element_a*–*element_b* is within tolerance."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'

    lengths: list[float] = []
    sites = struct.sites
    for i, si in enumerate(sites):
        if si.species_string != element_a:
            continue
        for j, sj in enumerate(sites):
            if j <= i and element_a == element_b:
                continue
            if j == i:
                continue
            if sj.species_string != element_b:
                continue
            if isinstance(struct, Molecule):
                dist = si.distance(sj)
            else:
                dist = struct.get_distance(i, j)
            if dist < cutoff_A:
                lengths.append(dist)

    if not lengths:
        return (
            False,
            f'{fpath.name}: no {element_a}-{element_b} bonds found within {cutoff_A} Å',
        )

    mean_len = float(np.mean(lengths))
    hit = abs(mean_len - expected) <= tolerance
    return (
        hit,
        f'{fpath.name}: mean {element_a}-{element_b} bond length = {mean_len:.4f} Å '
        f'({len(lengths)} bonds), expected={expected}±{tolerance}',
    )


# ---------------------------------------------------------------------------
# 5. Bond angle
# ---------------------------------------------------------------------------


def _angle_deg(v1: np.ndarray, v2: np.ndarray) -> float:
    """Angle in degrees between two vectors."""
    cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-15)
    cos = np.clip(cos, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)))


def check_bond_angle(
    workspace_dir: str | Path,
    *,
    filename: str,
    triplet: list[str],
    expected_deg: float,
    tolerance_deg: float,
    cutoff_A: float = 3.0,
) -> tuple[bool, str]:
    """Check the mean angle A-B-C where *triplet* = [A, B, C].

    B is the vertex.  Only considers bonds A-B and C-B shorter than *cutoff_A*.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    if len(triplet) != 3:
        return False, f'triplet must have exactly 3 elements, got {len(triplet)}'

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'

    elem_a, elem_b, elem_c = triplet
    sites = struct.sites

    # Find vertex atoms (element B)
    b_indices = [i for i, s in enumerate(sites) if s.species_string == elem_b]
    a_indices = [i for i, s in enumerate(sites) if s.species_string == elem_a]
    c_indices = [i for i, s in enumerate(sites) if s.species_string == elem_c]

    angles: list[float] = []
    for bi in b_indices:
        # Find A neighbours
        a_nbrs = []
        for ai in a_indices:
            if ai == bi:
                continue
            if isinstance(struct, Molecule):
                d = sites[ai].distance(sites[bi])
            else:
                d = struct.get_distance(ai, bi)
            if d < cutoff_A:
                a_nbrs.append(ai)

        # Find C neighbours
        c_nbrs = []
        for ci in c_indices:
            if ci == bi:
                continue
            if isinstance(struct, Molecule):
                d = sites[ci].distance(sites[bi])
            else:
                d = struct.get_distance(ci, bi)
            if d < cutoff_A:
                c_nbrs.append(ci)

        for ai in a_nbrs:
            for ci in c_nbrs:
                if ai == ci:
                    continue
                # Compute vectors B->A and B->C
                if isinstance(struct, Molecule):
                    va = np.array(sites[ai].coords) - np.array(sites[bi].coords)
                    vc = np.array(sites[ci].coords) - np.array(sites[bi].coords)
                else:
                    # Use Cartesian coords (handles periodicity for within-cell)
                    va = np.array(sites[ai].coords) - np.array(sites[bi].coords)
                    vc = np.array(sites[ci].coords) - np.array(sites[bi].coords)
                angles.append(_angle_deg(va, vc))

    if not angles:
        return (
            False,
            f'{fpath.name}: no {elem_a}-{elem_b}-{elem_c} angle found '
            f'within cutoff {cutoff_A} Å',
        )

    mean_angle = float(np.mean(angles))
    hit = abs(mean_angle - expected_deg) <= tolerance_deg
    return (
        hit,
        f'{fpath.name}: mean {elem_a}-{elem_b}-{elem_c} angle = {mean_angle:.2f}°'
        f' ({len(angles)} triplets), expected={expected_deg}±{tolerance_deg}',
    )


# ---------------------------------------------------------------------------
# 6. Cell parameter (a, b, c, alpha, beta, gamma)
# ---------------------------------------------------------------------------


def check_cell_param(
    workspace_dir: str | Path,
    *,
    filename: str,
    param: str,
    expected: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Verify a lattice parameter (a/b/c/alpha/beta/gamma)."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'
    if isinstance(struct, Molecule):
        return False, f'{fpath.name} is a molecule, not a periodic structure'

    lattice = struct.lattice
    valid_params = {'a', 'b', 'c', 'alpha', 'beta', 'gamma'}
    if param not in valid_params:
        return False, f'unknown lattice param {param!r}; choose from {valid_params}'

    actual = getattr(lattice, param)
    hit = abs(actual - expected) <= tolerance
    return hit, f'{fpath.name}: {param}={actual:.4f}, expected={expected}±{tolerance}'


# ---------------------------------------------------------------------------
# 7. Stoichiometry ratio
# ---------------------------------------------------------------------------


def check_stoichiometry_ratio(
    workspace_dir: str | Path,
    *,
    filename: str,
    element_a: str,
    element_b: str,
    expected_ratio: float,
    tolerance: float,
) -> tuple[bool, str]:
    """Verify that count(element_a) / count(element_b) ≈ expected_ratio."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'

    comp = struct.composition
    count_a = comp.get(element_a, 0)
    count_b = comp.get(element_b, 0)
    if count_b == 0:
        return (
            False,
            f'{fpath.name}: element {element_b!r} not found in composition {comp}',
        )
    actual = count_a / count_b
    hit = abs(actual - expected_ratio) <= tolerance
    return (
        hit,
        f'{fpath.name}: {element_a}/{element_b} = {count_a}/{count_b} = {actual:.4f}, '
        f'expected={expected_ratio}±{tolerance}',
    )


# ---------------------------------------------------------------------------
# 8. Coordination number
# ---------------------------------------------------------------------------


def check_coordination_number(
    workspace_dir: str | Path,
    *,
    filename: str,
    center_element: str,
    expected: int,
    tolerance: float = 0,
    cutoff_A: float = 2.5,
) -> tuple[bool, str]:
    """Count neighbours within *cutoff_A* for atoms of *center_element*."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'

    sites = struct.sites
    center_indices = [
        i for i, s in enumerate(sites) if s.species_string == center_element
    ]
    if not center_indices:
        return False, f'{fpath.name}: element {center_element!r} not found'

    coord_numbers: list[int] = []
    for ci in center_indices:
        cn = 0
        for j, sj in enumerate(sites):
            if j == ci:
                continue
            if isinstance(struct, Molecule):
                d = sites[ci].distance(sj)
            else:
                d = struct.get_distance(ci, j)
            if d < cutoff_A:
                cn += 1
        coord_numbers.append(cn)

    mean_cn = float(np.mean(coord_numbers))
    hit = abs(mean_cn - expected) <= tolerance
    return (
        hit,
        f'{fpath.name}: mean coordination of {center_element} = {mean_cn:.1f} '
        f'({len(coord_numbers)} centers, cutoff={cutoff_A} Å), expected={expected}±{tolerance}',
    )


# ---------------------------------------------------------------------------
# 9. Layer count (z-coordinate clustering)
# ---------------------------------------------------------------------------


def check_layer_count(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected: int,
    tolerance: float = 0,
    axis: str = 'z',
    layer_tol_A: float = 0.25,
) -> tuple[bool, str]:
    """Count distinct atomic planes along *axis* using Cartesian coordinates.

    Sites are sorted along the axis. A new plane starts when a coordinate is farther
    than *layer_tol_A* from the **first** coordinate of the current plane (same-layer
    atoms may sit at slightly different positions along the axis).
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    tol = float(layer_tol_A)
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'
    if isinstance(struct, Molecule):
        return False, f'{fpath.name} is a molecule, not a periodic structure'

    axis_map = {'x': 0, 'y': 1, 'z': 2}
    if axis.lower() not in axis_map:
        return False, f'axis must be x/y/z, got {axis!r}'
    ax = axis_map[axis.lower()]

    coords = np.array([s.coords[ax] for s in struct.sites])
    coords_sorted = np.sort(coords)
    if len(coords_sorted) < 2:
        return False, f'{fpath.name}: fewer than 2 atoms'

    # Count distinct planes: merge atoms within layer_tol_A of the current plane anchor.
    anchor = float(coords_sorted[0])
    n_layers = 1
    for c in coords_sorted[1:]:
        z = float(c)
        if z - anchor > tol:
            n_layers += 1
            anchor = z

    hit = abs(n_layers - expected) <= tolerance
    return (
        hit,
        f'{fpath.name}: {n_layers} layers along {axis} (layer_tol={tol} Å), '
        f'expected={expected}±{tolerance}',
    )


# ---------------------------------------------------------------------------
# 10. Surface termination — check outermost layer element along an axis
# ---------------------------------------------------------------------------


def check_surface_termination(
    workspace_dir: str | Path,
    *,
    filename: str,
    element: str,
    axis: str = 'z',
    side: str = 'top',
    layer_tol_A: float = 0.5,
) -> tuple[bool, str]:
    """Verify that the outermost atomic layer of a slab is of the given element.

    Parameters
    ----------
    filename : str
        Glob pattern or exact name of the slab structure file.
    element : str
        Element symbol expected at the outermost layer (e.g. ``'O'``).
    axis : str
        Slab stacking axis: ``'x'``, ``'y'``, or ``'z'``.
    side : str
        Which surface to check: ``'top'`` (maximum coord), ``'bottom'``
        (minimum coord), or ``'both'``.
    layer_tol_A : float
        Thickness tolerance in Å used to define the outermost layer.
        Atoms within *layer_tol_A* of the extremal atom are considered
        part of the outermost layer.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'no file matching {filename!r} in {root}'
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f'could not parse {fpath.name}: {exc}'
    if isinstance(struct, Molecule):
        return False, f'{fpath.name} is a molecule, not a periodic structure'

    axis_map = {'x': 0, 'y': 1, 'z': 2}
    if axis.lower() not in axis_map:
        return False, f'axis must be x/y/z, got {axis!r}'
    ax = axis_map[axis.lower()]

    sites = struct.sites
    coords = np.array([s.coords[ax] for s in sites])
    elements = [s.species_string for s in sites]

    def _check_side(extreme_coord: float) -> tuple[bool, str]:
        mask = np.abs(coords - extreme_coord) <= layer_tol_A
        layer_elems = [elements[i] for i in range(len(sites)) if mask[i]]
        if not layer_elems:
            return (
                False,
                f'{fpath.name}: outermost layer is empty (tol={layer_tol_A} Å)',
            )
        unique = sorted(set(layer_elems))
        has_elem = element in layer_elems
        return (
            has_elem,
            f'{fpath.name}: outermost {axis}-layer ({extreme_coord:.3f} Å) elements: '
            f'{unique}, expected {element!r}',
        )

    sides_to_check = []
    if side in ('top', 'both'):
        sides_to_check.append(float(np.max(coords)))
    if side in ('bottom', 'both'):
        sides_to_check.append(float(np.min(coords)))
    if not sides_to_check:
        return False, f"side must be 'top', 'bottom', or 'both', got {side!r}"

    results = [_check_side(c) for c in sides_to_check]
    failed = [(ok, msg) for ok, msg in results if not ok]
    if failed:
        return failed[0]
    msgs = '; '.join(msg for _, msg in results)
    return True, msgs


# ---------------------------------------------------------------------------
# File-count check (no pymatgen needed)
# ---------------------------------------------------------------------------


def check_file_count(
    workspace_dir: str | Path,
    *,
    pattern: str,
    expected: int,
    tolerance: int = 0,
) -> tuple[bool, str]:
    """Count files matching *pattern* (fnmatch glob) inside *workspace_dir*.

    Useful for verifying that the agent produced the expected number of output
    structure files (e.g. 5 ordered-replica CIFs).
    """
    root = Path(workspace_dir)
    if not root.is_dir():
        return False, f'workspace {root} does not exist or is not a directory'

    hits = [
        p for p in root.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, pattern)
    ]
    n = len(hits)
    ok = abs(n - expected) <= tolerance
    return (
        ok,
        f'{n} file(s) matching {pattern!r} in workspace (expected={expected}±{tolerance})',
    )
