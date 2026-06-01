"""Pymatgen-backed programmatic checks for structure-construction evaluation items.

Requires optional dependency group ``calculation`` (includes ``pymatgen``).
All public functions follow the ``(bool, str)`` return convention used by the
MATTER evaluator.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# Lazy optional-dep imports (numpy, pymatgen)

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

_IMPORT_MSG = "pymatgen not installed; install with: uv sync --extra calculation"


# File resolution helpers


def _resolve_file(workspace: Path, pattern: str) -> Path | None:
    """Return the best-matching file inside *workspace* for *pattern*.

    Resolution order:
    1. Exact filename match (case-sensitive).
    2. **Recursive** ``fnmatch`` glob expansion – newest file wins.
       The pattern is matched against the *basename* so that files inside
       subdirectories (e.g. ``calc_001/POSCAR``) are found too.
    """
    exact = workspace / pattern
    if exact.is_file():
        return exact

    hits = [
        p
        for p in workspace.rglob("*")
        if p.is_file() and fnmatch.fnmatch(p.name, pattern)
    ]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _resolve_files(workspace: Path, pattern: str) -> list[Path]:
    """Return all files matching *pattern* inside *workspace*.

    Exact paths take precedence for non-glob filenames. Otherwise the pattern is
    matched recursively against basenames, consistent with :func:`_resolve_file`.
    """
    exact = workspace / pattern
    if exact.is_file():
        return [exact]

    return sorted(
        [
            p
            for p in workspace.rglob("*")
            if p.is_file() and fnmatch.fnmatch(p.name, pattern)
        ]
    )


def _load_structure(path: Path) -> Structure | Molecule:
    """Read a CIF / POSCAR / XYZ / … file via pymatgen auto-detection."""
    suffix = path.suffix.lower()
    if suffix in {".xyz"}:
        return Molecule.from_file(str(path))
    return Structure.from_file(str(path))


# 1. Atom count


def check_atom_count(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected: int,
    tolerance: float = 0,
    element: str | None = None,
) -> tuple[bool, str]:
    """Verify total atoms, or atom count of a specific element when provided."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if element:
        actual = float(struct.composition.get(str(element), 0))
        label = f"{element}_count"
    else:
        actual = float(len(struct))
        label = "atom_count"
    hit = abs(actual - expected) <= tolerance
    return hit, f"{fpath.name}: {label}={actual:g}, expected={expected}±{tolerance}"


# 2. Formula


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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    comp = struct.composition
    # Compare both reduced and alphabetical formula representations
    actual_reduced = comp.reduced_formula
    actual_alpha = comp.alphabetical_formula
    actual_hill = comp.hill_formula

    from pymatgen.core import Composition

    try:
        expected_comp = Composition(formula)
    except Exception:
        return False, f"could not parse expected formula {formula!r}"

    # Normalise: compare element ratios
    if comp.reduced_composition == expected_comp.reduced_composition:
        return (
            True,
            f"{fpath.name}: formula={actual_reduced} matches expected {formula}",
        )
    return (
        False,
        f"{fpath.name}: formula={actual_reduced} (hill={actual_hill}, alpha={actual_alpha}) "
        f"does not match expected {formula}",
    )


def check_elements_present(
    workspace_dir: str | Path,
    *,
    filename: str,
    elements: list[str],
) -> tuple[bool, str]:
    """Verify that all listed elements are present in the structure."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    actual_elements = {str(el) for el in struct.composition.elements}
    required = set(elements)
    missing = required - actual_elements
    if missing:
        return (
            False,
            f"{fpath.name}: missing elements {sorted(missing)}, "
            f"found {sorted(actual_elements)}",
        )
    return (
        True,
        f"{fpath.name}: all required elements {sorted(required)} present "
        f"(found {sorted(actual_elements)})",
    )


# 3. Bond count (number of bonds between element pair shorter than cutoff)


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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

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
        f"{fpath.name}: {element_a}-{element_b} bonds (<{cutoff_A} Å) = {count}, "
        f"expected={expected_count}±{tolerance}",
    )


# 4. Representative bond length


def _collect_pair_distances(
    struct: Structure | Molecule,
    *,
    element_a: str,
    element_b: str,
    cutoff_A: float,
) -> list[float]:
    """Return all A-B distances < cutoff_A (avoiding double-count when A==B)."""
    sites = struct.sites
    same = element_a == element_b
    lengths: list[float] = []
    for i, si in enumerate(sites):
        if si.species_string != element_a:
            continue
        for j, sj in enumerate(sites):
            if j == i or sj.species_string != element_b:
                continue
            if same and j <= i:
                continue
            dist = (
                si.distance(sj)
                if isinstance(struct, Molecule)
                else struct.get_distance(i, j)
            )
            if dist < cutoff_A:
                lengths.append(dist)
    return lengths


def _load_struct_or_err(
    workspace_dir: str | Path, filename: str
) -> tuple[Path | None, Structure | Molecule | None, str]:
    if not _PMG_AVAILABLE:
        return None, None, _IMPORT_MSG
    fpath = _resolve_file(Path(workspace_dir), filename)
    if fpath is None:
        return None, None, f"no file matching {filename!r} in {workspace_dir}"
    try:
        return fpath, _load_structure(fpath), ""
    except Exception as exc:
        return fpath, None, f"could not parse {fpath.name}: {exc}"


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
    """Check that the mean A-B bond length is within tolerance."""
    fpath, struct, err = _load_struct_or_err(workspace_dir, filename)
    if struct is None:
        return False, err
    lengths = _collect_pair_distances(
        struct, element_a=element_a, element_b=element_b, cutoff_A=cutoff_A
    )
    if not lengths:
        return (
            False,
            f"{fpath.name}: no {element_a}-{element_b} bonds found within {cutoff_A} Å",
        )
    mean_len = float(np.mean(lengths))
    hit = abs(mean_len - expected) <= tolerance
    return hit, (
        f"{fpath.name}: mean {element_a}-{element_b} bond length = {mean_len:.4f} Å "
        f"({len(lengths)} bonds), expected={expected}±{tolerance}"
    )


def check_bond_length_range(
    workspace_dir: str | Path,
    *,
    filename: str,
    element_a: str,
    element_b: str,
    cutoff_A: float = 3.0,
    expected_min: float,
    expected_max: float,
) -> tuple[bool, str]:
    """Check that every A-B pair within ``cutoff_A`` lies in [min, max].

    Catches squeezed (<min) or stretched-but-still-bonded (>max) pairs that an
    over-aggressive or under-relaxed reconstruction leaves behind.
    """
    fpath, struct, err = _load_struct_or_err(workspace_dir, filename)
    if struct is None:
        return False, err
    lengths = _collect_pair_distances(
        struct, element_a=element_a, element_b=element_b, cutoff_A=cutoff_A
    )
    if not lengths:
        return (
            False,
            f"{fpath.name}: no {element_a}-{element_b} bonds found within {cutoff_A} Å",
        )
    min_len = float(np.min(lengths))
    max_len = float(np.max(lengths))
    hit = (min_len >= expected_min) and (max_len <= expected_max)
    return hit, (
        f"{fpath.name}: {element_a}-{element_b} bond lengths (<{cutoff_A} Å): "
        f"min={min_len:.4f} Å, max={max_len:.4f} Å ({len(lengths)} bonds), "
        f"expected all in [{expected_min}, {expected_max}] Å"
    )


# 5. Bond angle


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
    cutoff_a_b_A: float | None = None,
    cutoff_c_b_A: float | None = None,
    cutoff_a_b_min_A: float = 0.0,
    cutoff_c_b_min_A: float = 0.0,
) -> tuple[bool, str]:
    """Check the mean angle A-B-C where *triplet* = [A, B, C].

    B is the vertex.  An A-B pair is admitted as a bond when its (PBC-corrected,
    minimum-image) distance lies in ``[cutoff_a_b_min_A, cutoff_a_b_A]``; same
    for C-B with ``[cutoff_c_b_min_A, cutoff_c_b_A]``.

    The legacy single-cutoff API (``cutoff_A``) is kept for backwards
    compatibility: when ``cutoff_a_b_A`` / ``cutoff_c_b_A`` are not given they
    default to ``cutoff_A`` and the lower bounds default to 0 Å.

    The angle vectors are computed from minimum-image displacements (B->A and
    B->C) so that bonds crossing the periodic boundary are not corrupted.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    if len(triplet) != 3:
        return False, f"triplet must have exactly 3 elements, got {len(triplet)}"

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    cutoff_ab_max = float(cutoff_a_b_A) if cutoff_a_b_A is not None else float(cutoff_A)
    cutoff_cb_max = float(cutoff_c_b_A) if cutoff_c_b_A is not None else float(cutoff_A)
    cutoff_ab_min = float(cutoff_a_b_min_A)
    cutoff_cb_min = float(cutoff_c_b_min_A)

    elem_a, elem_b, elem_c = triplet
    sites = struct.sites

    b_indices = [i for i, s in enumerate(sites) if s.species_string == elem_b]
    a_indices = [i for i, s in enumerate(sites) if s.species_string == elem_a]
    c_indices = [i for i, s in enumerate(sites) if s.species_string == elem_c]

    is_periodic = not isinstance(struct, Molecule)
    cell_matrix = np.array(struct.lattice.matrix) if is_periodic else None
    cell_inv = np.linalg.inv(cell_matrix) if is_periodic else None

    def _mic_vec(disp: np.ndarray) -> np.ndarray:
        if not is_periodic:
            return disp
        f = disp @ cell_inv
        f -= np.round(f)
        return f @ cell_matrix

    angles: list[float] = []
    for bi in b_indices:
        a_nbrs = []
        for ai in a_indices:
            if ai == bi:
                continue
            if is_periodic:
                d = struct.get_distance(ai, bi)
            else:
                d = sites[ai].distance(sites[bi])
            if cutoff_ab_min <= d <= cutoff_ab_max:
                a_nbrs.append(ai)

        c_nbrs = []
        for ci in c_indices:
            if ci == bi:
                continue
            if is_periodic:
                d = struct.get_distance(ci, bi)
            else:
                d = sites[ci].distance(sites[bi])
            if cutoff_cb_min <= d <= cutoff_cb_max:
                c_nbrs.append(ci)

        b_coord = np.array(sites[bi].coords)
        for ai in a_nbrs:
            for ci in c_nbrs:
                if ai == ci:
                    continue
                va = _mic_vec(np.array(sites[ai].coords) - b_coord)
                vc = _mic_vec(np.array(sites[ci].coords) - b_coord)
                angles.append(_angle_deg(va, vc))

    if not angles:
        return (
            False,
            f"{fpath.name}: no {elem_a}-{elem_b}-{elem_c} angle found "
            f"within {elem_a}-{elem_b}=[{cutoff_ab_min},{cutoff_ab_max}] Å, "
            f"{elem_c}-{elem_b}=[{cutoff_cb_min},{cutoff_cb_max}] Å",
        )

    mean_angle = float(np.mean(angles))
    hit = abs(mean_angle - expected_deg) <= tolerance_deg
    return (
        hit,
        f"{fpath.name}: mean {elem_a}-{elem_b}-{elem_c} angle = {mean_angle:.2f}°"
        f" ({len(angles)} triplets), expected={expected_deg}±{tolerance_deg}",
    )


# 6. Cell parameter (a, b, c, alpha, beta, gamma)


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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if isinstance(struct, Molecule):
        return False, f"{fpath.name} is a molecule, not a periodic structure"

    lattice = struct.lattice
    valid_params = {"a", "b", "c", "alpha", "beta", "gamma"}
    if param not in valid_params:
        return False, f"unknown lattice param {param!r}; choose from {valid_params}"

    actual = getattr(lattice, param)
    hit = abs(actual - expected) <= tolerance
    return hit, f"{fpath.name}: {param}={actual:.4f}, expected={expected}±{tolerance}"


# 7. Stoichiometry ratio
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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    comp = struct.composition
    count_a = comp.get(element_a, 0)
    count_b = comp.get(element_b, 0)
    if count_b == 0:
        return (
            False,
            f"{fpath.name}: element {element_b!r} not found in composition {comp}",
        )
    actual = count_a / count_b
    hit = abs(actual - expected_ratio) <= tolerance
    return (
        hit,
        f"{fpath.name}: {element_a}/{element_b} = {count_a}/{count_b} = {actual:.4f}, "
        f"expected={expected_ratio}±{tolerance}",
    )


# 7b. Charge balance (formal oxidation states)


def check_charge_balance(
    workspace_dir: str | Path,
    *,
    filename: str,
    oxidation_states: dict[str, int],
    tolerance: float = 0.01,
) -> tuple[bool, str]:
    """Verify that sum(count_i * oxidation_i) == 0 for an ionic structure.

    oxidation_states: mapping of element symbol to formal charge, e.g.
    {"Mg": 2, "Al": 3, "O": -2}.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    comp = struct.composition
    total_charge = 0.0
    details = []
    for elem, ox in oxidation_states.items():
        count = comp.get(elem, 0)
        total_charge += count * ox
        details.append(f"{elem}({ox:+d})×{count:g}")

    hit = abs(total_charge) <= tolerance
    return (
        hit,
        f'{fpath.name}: charge = {total_charge:+.2f} [{", ".join(details)}]'
        f'{" — NEUTRAL" if hit else " — NOT NEUTRAL"}',
    )


# 8. Coordination number


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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"

    sites = struct.sites
    center_indices = [
        i for i, s in enumerate(sites) if s.species_string == center_element
    ]
    if not center_indices:
        return False, f"{fpath.name}: element {center_element!r} not found"

    coord_numbers: list[int] = []
    for ci in center_indices:
        if isinstance(struct, Molecule):
            # Non-periodic: count direct distances (no PBC images)
            cn = sum(
                1
                for j, sj in enumerate(sites)
                if j != ci and sites[ci].distance(sj) < cutoff_A
            )
        else:
            # Periodic structure: use get_neighbors which enumerates ALL
            # periodic images within the cutoff.  The naive get_distance(ci, j)
            # loop only returns one (MIC) distance per site-pair and therefore
            # misses cases where the same site has two images both within the
            # cutoff (e.g. a narrow cell where b ≈ 2 × bond_length).
            cn = len(struct.get_neighbors(struct[ci], cutoff_A))
        coord_numbers.append(cn)

    mean_cn = float(np.mean(coord_numbers))
    hit = abs(mean_cn - expected) <= tolerance
    return (
        hit,
        f"{fpath.name}: mean coordination of {center_element} = {mean_cn:.1f} "
        f"({len(coord_numbers)} centers, cutoff={cutoff_A} Å), expected={expected}±{tolerance}",
    )


# 9. Layer count (z-coordinate clustering)


def check_layer_count(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected: int,
    tolerance: float = 0,
    axis: str = "z",
    layer_tol_A: float = 0.25,
    element: str | None = None,
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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if isinstance(struct, Molecule):
        return False, f"{fpath.name} is a molecule, not a periodic structure"

    axis_map = {"x": 0, "y": 1, "z": 2}
    if axis.lower() not in axis_map:
        return False, f"axis must be x/y/z, got {axis!r}"
    ax = axis_map[axis.lower()]

    if element:
        coords = np.array(
            [
                s.coords[ax]
                for s in struct.sites
                if getattr(s.specie, "symbol", str(s.specie)) == element
            ]
        )
    else:
        coords = np.array([s.coords[ax] for s in struct.sites])
    coords_sorted = np.sort(coords)
    if len(coords_sorted) < 2:
        scope = f" for element {element}" if element else ""
        return False, f"{fpath.name}: fewer than 2 atoms{scope}"

    # Count distinct planes: merge atoms within layer_tol_A of the current plane anchor.
    anchor = float(coords_sorted[0])
    n_layers = 1
    for c in coords_sorted[1:]:
        z = float(c)
        if z - anchor > tol:
            n_layers += 1
            anchor = z

    hit = abs(n_layers - expected) <= tolerance
    scope = f" for element {element}" if element else ""
    return (
        hit,
        f"{fpath.name}: {n_layers} layers along {axis}{scope} (layer_tol={tol} Å), "
        f"expected={expected}±{tolerance}",
    )


# 10. Surface termination — check outermost layer element along an axis


def check_surface_termination(
    workspace_dir: str | Path,
    *,
    filename: str,
    element: str,
    axis: str = "z",
    side: str = "top",
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
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if isinstance(struct, Molecule):
        return False, f"{fpath.name} is a molecule, not a periodic structure"

    axis_map = {"x": 0, "y": 1, "z": 2}
    if axis.lower() not in axis_map:
        return False, f"axis must be x/y/z, got {axis!r}"
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
                f"{fpath.name}: outermost layer is empty (tol={layer_tol_A} Å)",
            )
        unique = sorted(set(layer_elems))
        has_elem = element in layer_elems
        return (
            has_elem,
            f"{fpath.name}: outermost {axis}-layer ({extreme_coord:.3f} Å) elements: "
            f"{unique}, expected {element!r}",
        )

    sides_to_check = []
    if side in ("top", "both"):
        sides_to_check.append(float(np.max(coords)))
    if side in ("bottom", "both"):
        sides_to_check.append(float(np.min(coords)))
    if not sides_to_check:
        return False, f"side must be 'top', 'bottom', or 'both', got {side!r}"

    results = [_check_side(c) for c in sides_to_check]
    failed = [(ok, msg) for ok, msg in results if not ok]
    if failed:
        return failed[0]
    msgs = "; ".join(msg for _, msg in results)
    return True, msgs


# File-count check (no pymatgen needed)


def check_file_count(
    workspace_dir: str | Path,
    *,
    pattern: str,
    expected: int,
    tolerance: int = 0,
) -> tuple[bool, str]:
    """Count files matching *pattern* (fnmatch glob) inside *workspace_dir*.

    Walks **recursively** so that files inside subdirectories are counted too.
    Useful for verifying that the agent produced the expected number of output
    structure files (e.g. 5 ordered-replica CIFs).
    """
    root = Path(workspace_dir)
    if not root.is_dir():
        return False, f"workspace {root} does not exist or is not a directory"

    hits = [
        p for p in root.rglob("*") if p.is_file() and fnmatch.fnmatch(p.name, pattern)
    ]
    n = len(hits)
    ok = abs(n - expected) <= tolerance
    return (
        ok,
        f"{n} file(s) matching {pattern!r} in workspace (expected={expected}±{tolerance})",
    )


# 11. Structure-file parseability


def check_parsable(
    workspace_dir: str | Path,
    *,
    filename: str,
) -> tuple[bool, str]:
    """Verify that every matching structure file can be parsed by pymatgen."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpaths = _resolve_files(root, filename)
    if not fpaths:
        return False, f"no file matching {filename!r} in {root}"

    parsed: list[str] = []
    for fpath in fpaths:
        try:
            _load_structure(fpath)
        except Exception as exc:
            return False, f"could not parse {fpath.name}: {exc}"
        parsed.append(fpath.name)

    return True, f"parsed {len(parsed)} structure file(s): {parsed}"


# 12. Occupancy check for ordered CIF replicas


def check_all_occupancy_one(
    workspace_dir: str | Path,
    *,
    filename: str,
    tolerance: float = 1e-6,
) -> tuple[bool, str]:
    """Verify that all species occupancies in every matching file are 1.

    This is intentionally a file-level check for ordered replicas. A disordered
    site with split species such as ``A0.5 B0.5`` fails even though total site
    occupancy sums to 1.
    """
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpaths = _resolve_files(root, filename)
    if not fpaths:
        return False, f"no file matching {filename!r} in {root}"

    checked_sites = 0
    for fpath in fpaths:
        try:
            struct = _load_structure(fpath)
        except Exception as exc:
            return False, f"could not parse {fpath.name}: {exc}"

        for idx, site in enumerate(struct.sites):
            species_items = list(site.species.items())
            if len(species_items) != 1:
                return (
                    False,
                    f"{fpath.name}: site {idx} has split species "
                    f"{site.species_string}, expected a single occupancy-1 species",
                )
            specie, occ = species_items[0]
            if abs(float(occ) - 1.0) > tolerance:
                return (
                    False,
                    f"{fpath.name}: site {idx} species {specie} occupancy={float(occ):g}, "
                    f"expected 1±{tolerance}",
                )
            checked_sites += 1

    return (
        True,
        f"all occupancies are 1±{tolerance} across {checked_sites} site(s) "
        f"in {len(fpaths)} file(s)",
    )


# 13. Space group number


def check_space_group(
    workspace_dir: str | Path,
    *,
    filename: str,
    expected_number: int | list[int],
    symprec: float = 0.1,
    angle_tolerance: float = 5.0,
) -> tuple[bool, str]:
    """Verify the space-group number of a periodic structure file."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    if isinstance(struct, Molecule):
        return False, f"{fpath.name} is a molecule, not a periodic structure"

    try:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        analyzer = SpacegroupAnalyzer(
            struct, symprec=symprec, angle_tolerance=angle_tolerance
        )
        actual_number = int(analyzer.get_space_group_number())
        actual_symbol = analyzer.get_space_group_symbol()
    except Exception as exc:
        return False, f"could not determine space group for {fpath.name}: {exc}"

    allowed = (
        expected_number if isinstance(expected_number, list) else [expected_number]
    )
    ok = actual_number in allowed
    expected_str = "/".join(f"#{n}" for n in allowed)
    return (
        ok,
        f"{fpath.name}: space group #{actual_number} ({actual_symbol}), "
        f"expected {expected_str} (symprec={symprec}, angle_tolerance={angle_tolerance})",
    )


def check_composition(
    workspace_dir: str | Path,
    *,
    filename: str,
    must_contain_elements: list[str] | None = None,
    must_not_contain_elements: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify structure contains (or excludes) specified elements."""
    if not _PMG_AVAILABLE:
        return False, _IMPORT_MSG
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching {filename!r} in {root}"
    try:
        struct = _load_structure(fpath)
    except Exception as exc:
        return False, f"could not parse {fpath.name}: {exc}"
    actual_elements = {str(el) for el in struct.composition.elements}
    must_contain = set(must_contain_elements or [])
    must_not = set(must_not_contain_elements or [])
    missing = must_contain - actual_elements
    if missing:
        return False, (
            f"{fpath.name}: missing required elements {sorted(missing)}, "
            f"found {sorted(actual_elements)}"
        )
    unwanted = must_not & actual_elements
    if unwanted:
        return False, (
            f"{fpath.name}: contains forbidden elements {sorted(unwanted)}, "
            f"found {sorted(actual_elements)}"
        )
    return True, (
        f"{fpath.name}: composition check passed — "
        f"elements {sorted(actual_elements)} "
        f"(required: {sorted(must_contain)})"
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
        nn = dists[:n_neighbors] if n_neighbors > 0 else [d for d in dists if d <= max_distance * 1.5]
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


from evaluation.validators.structure_distance import (  # noqa: E402, F401
    check_min_interatomic_distance,
)
