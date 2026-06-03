"""Planarity check for rigid fused-aromatic / conjugated molecular cores.

Requires optional dependency group ``calculation`` (numpy + pymatgen).
"""

from __future__ import annotations

from pathlib import Path

from evaluation.validators.structure_general import (
    _IMPORT_MSG,
    _NP_AVAILABLE,
    _PMG_AVAILABLE,
    _load_structure,
    _resolve_file,
    np,
)


def check_planarity(
    workspace_dir: str | Path,
    *,
    filename: str,
    max_rms_A: float = 0.3,
    aromatic_cc_cutoff_A: float = 1.46,
    min_core_atoms: int = 8,
    element: str = "C",
) -> tuple[bool, str]:
    """Verify that the rigid conjugated/aromatic core of a molecule is planar.

    Designed for molecules with a known-planar fused-aromatic core (e.g. perylene
    diimide, porphyrin, PAHs) built from SMILES + 3D embedding. RDKit/ETKDG/UFF
    embedding can return a *folded* conformer that keeps the correct chemical
    connectivity yet badly violates core planarity; this check catches that.

    Procedure:
      1. Select carbon atoms with >= 2 carbon neighbours within
         ``aromatic_cc_cutoff_A`` (default 1.46 Å). This isolates conjugated
         aromatic C-C (~1.34-1.43 Å) and excludes sp3 alkyl C-C (~1.52 Å).
      2. Keep the largest connected component of those atoms (the fused core).
      3. Fit a best-fit plane via SVD and compute the RMS out-of-plane deviation.
      4. Pass when RMS <= ``max_rms_A``.

    A planar/ideal perylene core gives RMS < ~0.1 Å; a folded core (the real
    failure mode) gives RMS ~1 Å or larger.
    """
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

    coords = np.array([site.coords for site in struct.sites], dtype=float)
    symbols = [site.species_string for site in struct.sites]
    core_candidates = [i for i, s in enumerate(symbols) if s == element]
    if len(core_candidates) < min_core_atoms:
        return False, (
            f"{fpath.name}: only {len(core_candidates)} {element} atoms found, "
            f"cannot locate an aromatic core (need >= {min_core_atoms})"
        )

    # Adjacency among carbons within aromatic C-C distance.
    adj: dict[int, list[int]] = {i: [] for i in core_candidates}
    for idx_a in range(len(core_candidates)):
        i = core_candidates[idx_a]
        for idx_b in range(idx_a + 1, len(core_candidates)):
            j = core_candidates[idx_b]
            d = float(np.linalg.norm(coords[i] - coords[j]))
            if 0.5 < d <= aromatic_cc_cutoff_A:
                adj[i].append(j)
                adj[j].append(i)

    aromatic = [i for i in core_candidates if len(adj[i]) >= 2]
    if len(aromatic) < min_core_atoms:
        return False, (
            f"{fpath.name}: found only {len(aromatic)} candidate aromatic {element} "
            f"atoms (>=2 neighbours within {aromatic_cc_cutoff_A} Å); "
            "no fused conjugated core detected"
        )

    # Largest connected component among the aromatic-core atoms.
    aromatic_set = set(aromatic)
    seen: set[int] = set()
    best: list[int] = []
    for start in aromatic:
        if start in seen:
            continue
        stack = [start]
        comp: list[int] = []
        seen.add(start)
        while stack:
            node = stack.pop()
            comp.append(node)
            for nb in adj[node]:
                if nb in aromatic_set and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(comp) > len(best):
            best = comp

    if len(best) < min_core_atoms:
        return False, (
            f"{fpath.name}: largest fused-aromatic component has {len(best)} atoms "
            f"(need >= {min_core_atoms})"
        )

    core = coords[np.array(best)]
    centred = core - core.mean(axis=0)
    # Plane normal = singular vector of smallest singular value.
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    out_of_plane = centred @ vh[-1]
    rms = float(np.sqrt(np.mean(out_of_plane**2)))
    max_dev = float(np.max(np.abs(out_of_plane)))

    hit = rms <= max_rms_A
    return hit, (
        f"{fpath.name}: aromatic core = {len(best)} {element} atoms, "
        f"out-of-plane RMS={rms:.3f} Å (max={max_dev:.3f} Å), "
        f"threshold={max_rms_A} Å -> {'planar' if hit else 'FOLDED/non-planar'}"
    )
