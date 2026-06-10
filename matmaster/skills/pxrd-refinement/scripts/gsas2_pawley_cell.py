"""Cell standardisation helpers for gsas2_pawley.py."""

from __future__ import annotations

import logging

import numpy as np

_SIN_GAMMA_FLOOR = 1e-3

_CELL_FIELDS = ("a", "b", "c", "alpha", "beta", "gamma", "volume")
_ESD_FIELDS = ("a_esd", "b_esd", "c_esd", "alpha_esd", "beta_esd", "gamma_esd")
_LOGGER = logging.getLogger("gsas2_pawley")


# ---------------------------------------------------------------------------
# Cell standardisation — spglib Niggli reduction + reference-cell alignment
# ---------------------------------------------------------------------------

# Axis permutations: (a,b,c,α,β,γ) index mapping for all 6 orderings.
# α = angle(b,c), β = angle(a,c), γ = angle(a,b).
_AXIS_PERMS = [
    (0, 1, 2, 3, 4, 5),  # a  b  c  α β γ  (identity)
    (0, 2, 1, 4, 3, 5),  # a  c  b  β α γ
    (1, 0, 2, 3, 5, 4),  # b  a  c  α γ β
    (1, 2, 0, 5, 3, 4),  # b  c  a  γ α β
    (2, 0, 1, 4, 5, 3),  # c  a  b  β γ α
    (2, 1, 0, 5, 4, 3),  # c  b  a  γ β α
]


def cell_to_lattice(cell: list[float]) -> np.ndarray:
    """[a,b,c,α,β,γ] (Å/deg) → 3×3 row-vector lattice matrix."""
    a, b, c, alpha, beta, gamma = cell
    ar, br, gr = np.radians(alpha), np.radians(beta), np.radians(gamma)
    sin_gamma = float(np.sin(gr))
    if abs(sin_gamma) < _SIN_GAMMA_FLOOR:
        raise ValueError(
            f"cell gamma={gamma}° too close to 0/180; cannot build lattice safely"
        )
    bx = b * np.cos(gr)
    by = b * sin_gamma
    cx = c * np.cos(br)
    cy = c * (np.cos(ar) - np.cos(br) * np.cos(gr)) / sin_gamma
    cz = np.sqrt(max(c * c - cx * cx - cy * cy, 0.0))
    return np.array([[a, 0.0, 0.0], [bx, by, 0.0], [cx, cy, cz]])


def lattice_to_cell(L: np.ndarray) -> list[float]:
    """3×3 row-vector lattice matrix → [a,b,c,α,β,γ]."""
    va, vb, vc = L[0], L[1], L[2]
    a, b, c = (np.linalg.norm(v) for v in (va, vb, vc))
    alpha = np.degrees(np.arccos(np.clip(np.dot(vb, vc) / (b * c), -1, 1)))
    beta = np.degrees(np.arccos(np.clip(np.dot(va, vc) / (a * c), -1, 1)))
    gamma = np.degrees(np.arccos(np.clip(np.dot(va, vb) / (a * b), -1, 1)))
    return [float(a), float(b), float(c), float(alpha), float(beta), float(gamma)]


def _record_warning(warnings_out: list | None, message: str) -> None:
    if warnings_out is not None:
        warnings_out.append(message)
    else:
        _LOGGER.warning(message)


def niggli_reduce_cell(
    cell: list[float], *, warnings_out: list | None = None
) -> list[float]:
    """Niggli-reduce a cell using spglib, with fallback to identity."""
    try:
        import spglib

        L = cell_to_lattice(cell)
        L_reduced = spglib.niggli_reduce(L)
        if L_reduced is None:
            return list(cell)
        return lattice_to_cell(L_reduced)
    except ImportError:
        _record_warning(
            warnings_out,
            "spglib not available; Niggli reduction skipped",
        )
        return list(cell)
    except ValueError as exc:
        _record_warning(
            warnings_out,
            f"Niggli reduction skipped: {exc}",
        )
        return list(cell)


def _cell_distance_weighted(c1: list[float], c2: list[float]) -> float:
    """Distance between two cells, weighting lengths (Å) and angles (°)."""
    d = 0.0
    for i in range(3):
        d += (c1[i] - c2[i]) ** 2
    for i in range(3, 6):
        d += ((c1[i] - c2[i]) / 10.0) ** 2
    return d


def cell_volume(cell_list: list[float]) -> float:
    """Compute unit-cell volume from [a,b,c,alpha,beta,gamma]."""
    a, b, c, alpha, beta, gamma = cell_list
    ar, br, gr = np.radians(alpha), np.radians(beta), np.radians(gamma)
    vol = (
        a
        * b
        * c
        * np.sqrt(
            1
            - np.cos(ar) ** 2
            - np.cos(br) ** 2
            - np.cos(gr) ** 2
            + 2 * np.cos(ar) * np.cos(br) * np.cos(gr)
        )
    )
    return float(vol)


def _enumerate_equivalent_settings(
    cell: list[float],
) -> list[tuple[list[float], tuple[int, ...], tuple[bool, bool, bool]]]:
    """Generate all equivalent cell settings via axis permutation + angle supplement."""
    out: list[tuple[list[float], tuple[int, ...], tuple[bool, bool, bool]]] = []
    for perm in _AXIS_PERMS:
        base = [cell[perm[i]] for i in range(6)]
        queue: list[tuple[list[float], tuple[bool, bool, bool]]] = [
            (base, (False, False, False))
        ]
        for ang_idx in (3, 4, 5):
            expanded: list[tuple[list[float], tuple[bool, bool, bool]]] = []
            for v, supplemented in queue:
                expanded.append((v, supplemented))
                if abs(v[ang_idx] - 90.0) > 0.5:
                    alt = list(v)
                    alt[ang_idx] = 180.0 - alt[ang_idx]
                    mask = list(supplemented)
                    mask[ang_idx - 3] = True
                    expanded.append((alt, tuple(mask)))
            queue = expanded
        out.extend((candidate, perm, supplemented) for candidate, supplemented in queue)
    return out


def standardize_cell(result: dict, ref_cell: list[float], niggli: bool = False) -> dict:
    """Standardise refined cell to the same setting as *ref_cell*.

    Enumerates axis permutations × angle-supplement equivalences of
    the refined cell (and optionally its Niggli-reduced form) and picks
    the setting closest to *ref_cell*.

    Operates in-place and returns the same dict.
    """
    cur = [
        result["a"],
        result["b"],
        result["c"],
        result["alpha"],
        result["beta"],
        result["gamma"],
    ]

    warnings = result.setdefault("warnings", [])
    sources: list[tuple[list[float], bool]] = [(cur, True)]
    if niggli:
        sources.append((niggli_reduce_cell(cur, warnings_out=warnings), False))

    candidates: list[
        tuple[list[float], tuple[int, ...], tuple[bool, bool, bool], bool]
    ] = []
    for src, esd_from_original in sources:
        candidates.extend(
            (cand, perm, supplemented, esd_from_original)
            for cand, perm, supplemented in _enumerate_equivalent_settings(src)
        )

    best = cur
    best_perm: tuple[int, ...] | None = None
    best_supplemented: tuple[bool, bool, bool] | None = None
    best_esd_from_original = True
    best_d = _cell_distance_weighted(cur, ref_cell)
    for cand, perm, supplemented, esd_from_original in candidates:
        d = _cell_distance_weighted(cand, ref_cell)
        if d < best_d - 1e-8:
            best_d = d
            best = cand
            best_perm = perm
            best_supplemented = supplemented
            best_esd_from_original = esd_from_original

    if best is not cur:
        result["a"] = round(best[0], 5)
        result["b"] = round(best[1], 5)
        result["c"] = round(best[2], 5)
        result["alpha"] = round(best[3], 4)
        result["beta"] = round(best[4], 4)
        result["gamma"] = round(best[5], 4)

        old_esds = [result.get(f) for f in _ESD_FIELDS]
        if all(e is not None for e in old_esds):
            if best_esd_from_original and best_perm is not None:
                reordered = [old_esds[best_perm[i]] for i in range(6)]
                for field, val in zip(_ESD_FIELDS, reordered):
                    result[field] = val
            else:
                for field in _ESD_FIELDS:
                    result[field] = None
                warnings.append(
                    "ESDs cleared because Niggli cell standardisation changed "
                    "the basis beyond an explicit axis permutation"
                )

        if any(best_supplemented or (False, False, False)):
            result["standardize_cell_angle_supplements"] = {
                "alpha": bool(best_supplemented[0]),
                "beta": bool(best_supplemented[1]),
                "gamma": bool(best_supplemented[2]),
            }

        result["volume"] = round(cell_volume(best), 4)

    return result


# ---------------------------------------------------------------------------
# GSAS-II kernel helpers (only usable when GSAS-II is on sys.path)
# ---------------------------------------------------------------------------
