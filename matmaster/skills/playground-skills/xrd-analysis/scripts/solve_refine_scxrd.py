#!/usr/bin/env python3
"""
solve_refine_scxrd.py — Single-crystal XRD structure solution, refinement & CIF.

Pipeline:
  1. Parse HKL file (SHELX HKLF4) and P4P/INS file (cell, space group)
  2. Try SHELX (shelxs+shelxl) if installed  →  best quality
  3. Fallback: Python charge-flipping + least-squares refinement
  4. Write CIF and print JSON summary

Dependencies: numpy, scipy.
Optional:     pymatgen (space group ops), shelxs/shelxl (preferred if on PATH).

Usage:
  python solve_refine_scxrd.py --hkl data.hkl --p4p crystal.p4p -o refined.cif
  python solve_refine_scxrd.py --hkl data.hkl --ins crystal.ins -o refined.cif
  python solve_refine_scxrd.py --hkl data.hkl --cell "12 8 14 90 95 90" \\
         --sg P21 --wavelength 0.71073 -o refined.cif
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

# ═══════════════════════════════════════════════════════════════════════
# Atomic scattering factors — Cromer-Mann 4-Gaussian + constant
# f(s) = Σ a_i exp(-b_i s²) + c,  s = sinθ/λ
# ═══════════════════════════════════════════════════════════════════════
_SF = {
    "H": ([0.4899, 0.2620, 0.1968, 0.0499], [20.659, 7.740, 49.552, 2.202], 0.0013),
    "C": ([2.3100, 1.0200, 1.5886, 0.8650], [20.844, 10.208, 0.569, 51.651], 0.2156),
    "N": ([12.213, 3.1322, 2.0125, 1.1663], [0.006, 9.893, 28.998, 0.583], -11.529),
    "O": ([3.0485, 2.2868, 1.5463, 0.8670], [13.277, 5.701, 0.324, 32.909], 0.2508),
    "F": ([3.5392, 2.6412, 1.5170, 1.0243], [10.283, 4.294, 0.262, 26.148], 0.2776),
    "Na": ([4.7626, 3.1736, 1.2674, 1.1128], [3.285, 8.842, 0.314, 129.42], 0.676),
    "Mg": ([5.4204, 2.1735, 1.2269, 2.3073], [2.828, 79.261, 0.381, 7.194], 0.8584),
    "Al": ([6.4202, 1.9002, 1.5936, 1.9646], [3.039, 0.743, 31.547, 85.089], 1.1151),
    "Si": ([6.2915, 3.0353, 1.9891, 1.5410], [2.439, 32.334, 0.679, 81.694], 1.1407),
    "P": ([6.4345, 4.1791, 1.7800, 1.4908], [1.907, 27.157, 0.526, 68.165], 1.1149),
    "S": ([6.9053, 5.2034, 1.4379, 1.5863], [1.468, 22.215, 0.254, 56.172], 0.8669),
    "Cl": ([11.460, 7.1964, 6.2556, 1.6455], [0.010, 1.166, 18.519, 47.778], -9.5574),
    "K": ([8.2186, 7.4398, 1.0519, 0.8659], [12.795, 0.775, 213.19, 41.684], 1.4228),
    "Ca": ([8.6266, 7.3873, 1.5899, 1.0211], [10.442, 0.660, 85.748, 178.44], 1.3751),
    "Ti": ([9.7595, 7.3558, 1.6991, 1.9021], [7.851, 0.500, 35.634, 116.11], 1.2807),
    "Mn": ([11.282, 7.3573, 3.0193, 2.2441], [5.341, 0.343, 17.867, 83.754], 1.0896),
    "Fe": ([11.770, 7.3573, 3.5222, 2.3045], [4.761, 0.307, 15.353, 76.881], 1.0369),
    "Co": ([12.284, 7.3409, 4.0034, 2.3488], [4.279, 0.278, 13.536, 71.169], 1.0118),
    "Ni": ([12.838, 7.2920, 4.4438, 2.3800], [3.878, 0.257, 12.176, 66.342], 1.0341),
    "Cu": ([13.338, 7.1676, 5.6158, 1.6735], [3.583, 0.247, 11.397, 64.812], 1.1910),
    "Zn": ([14.074, 7.0318, 5.1652, 2.4100], [3.266, 0.233, 10.316, 58.710], 1.3041),
    "Ga": ([15.235, 6.7006, 4.3591, 2.9623], [3.067, 0.241, 10.781, 61.414], 1.7189),
    "Ge": ([16.082, 6.3747, 3.7068, 3.6830], [2.851, 0.252, 11.447, 54.763], 2.1313),
    "As": ([16.672, 6.0701, 3.4313, 4.2779], [2.635, 0.265, 12.948, 47.797], 2.531),
    "Se": ([17.001, 5.8196, 3.9731, 4.3543], [2.409, 0.273, 15.237, 43.816], 2.8409),
    "Br": ([17.179, 5.2358, 5.6377, 3.9851], [2.172, 16.580, 0.261, 41.433], 2.9557),
    "Mo": ([3.7025, 17.236, 12.888, 3.7426], [0.277, 1.096, 11.004, 61.658], 4.3875),
    "Ag": ([19.281, 17.267, 4.6028, 2.5860], [0.645, 5.978, 26.897, 84.129], 5.266),
    "Cd": ([19.222, 17.644, 4.461, 2.5636], [0.595, 5.423, 24.695, 80.572], 5.098),
    "Sn": ([19.189, 18.559, 4.4585, 2.4668], [5.831, 0.376, 26.891, 83.957], 4.782),
    "I": ([20.147, 18.995, 7.5138, 2.2735], [4.347, 0.381, 27.766, 66.878], 4.0712),
    "Ba": ([20.338, 19.029, 7.0136, 2.3410], [3.216, 0.276, 15.073, 78.550], 4.264),
    "W": ([29.081, 15.430, 14.433, 5.1198], [1.721, 9.370, 0.322, 57.057], -0.098),
    "Pt": ([27.006, 17.764, 15.713, 5.7837], [1.513, 8.812, 0.323, 48.009], 1.735),
    "Au": ([16.882, 18.591, 25.558, 5.8600], [0.461, 8.622, 1.483, 36.396], 12.066),
    "Pb": ([31.062, 13.064, 18.442, 5.9696], [0.691, 2.358, 8.618, 47.258], 13.412),
    "Bi": ([33.369, 12.951, 16.588, 6.4692], [0.704, 2.924, 8.794, 48.009], 13.578),
}


def _scatt(element: str, s_sq: np.ndarray) -> np.ndarray:
    """Atomic scattering factor f(sin²θ/λ²)."""
    a, b, c = _SF.get(element, _SF["C"])
    f = np.full_like(s_sq, c, dtype=float)
    for ai, bi in zip(a, b):
        f += ai * np.exp(-bi * s_sq)
    return f


# ═══════════════════════════════════════════════════════════════════════
# Cell geometry helpers
# ═══════════════════════════════════════════════════════════════════════
def _cell_volume(cell):
    a, b, c, al, be, ga = cell
    ar, br, gr = np.radians(al), np.radians(be), np.radians(ga)
    ca, cb, cg = np.cos(ar), np.cos(br), np.cos(gr)
    return a * b * c * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg)


def _d_star_sq(hkl, cell):
    """Return 1/d² array for every reflection."""
    a, b, c, al, be, ga = cell
    ar, br, gr = np.radians(al), np.radians(be), np.radians(ga)
    ca, cb, cg = np.cos(ar), np.cos(br), np.cos(gr)
    sa, sb, sg = np.sin(ar), np.sin(br), np.sin(gr)
    V = _cell_volume(cell)
    astar, bstar, cstar = b * c * sa / V, a * c * sb / V, a * b * sg / V
    cas = (cb * cg - ca) / (sb * sg)
    cbs = (ca * cg - cb) / (sa * sg)
    cgs = (ca * cb - cg) / (sa * sb)
    h, k, ell = (
        hkl[:, 0].astype(float),
        hkl[:, 1].astype(float),
        hkl[:, 2].astype(float),
    )
    return (
        (h * astar) ** 2
        + (k * bstar) ** 2
        + (ell * cstar) ** 2
        + 2 * h * k * astar * bstar * cgs
        + 2 * h * ell * astar * cstar * cbs
        + 2 * k * ell * bstar * cstar * cas
    )


# ═══════════════════════════════════════════════════════════════════════
# Data I/O
# ═══════════════════════════════════════════════════════════════════════
def parse_hkl(path: str) -> dict:
    """Read SHELX-format HKL (HKLF 4): h k l F² σ(F²)."""
    rows = []
    with open(path) as f:
        for line in f:
            s = line.rstrip()
            if not s:
                continue
            # Try free-format first
            parts = s.split()
            ok = False
            if len(parts) >= 5:
                try:
                    h, k, ell = int(parts[0]), int(parts[1]), int(parts[2])
                    fsq, sig = float(parts[3]), float(parts[4])
                    ok = True
                except ValueError:
                    pass
            # Fall back to fixed-width (I4, I4, I4, F8, F8)
            if not ok and len(s) >= 28:
                try:
                    h = int(s[0:4])
                    k = int(s[4:8])
                    ell = int(s[8:12])
                    fsq = float(s[12:20])
                    sig = float(s[20:28])
                    ok = True
                except ValueError:
                    pass
            if not ok:
                continue
            if h == 0 and k == 0 and ell == 0:
                break
            rows.append((h, k, ell, fsq, sig))
    if not rows:
        raise ValueError(f"No reflections parsed from {path}")
    arr = np.array(rows)
    return {"hkl": arr[:, :3].astype(int), "fsq": arr[:, 3], "sigma": arr[:, 4]}


def parse_p4p(path: str) -> dict:
    cell = wl = sg = None
    with open(path) as f:
        for line in f:
            tok = line.split()
            if not tok:
                continue
            key = tok[0].upper()
            if key == "CELL" and len(tok) >= 7:
                cell = [float(x) for x in tok[1:7]]
            elif key in ("CTYPE", "SOURCE") and wl is None:
                txt = " ".join(tok[1:]).upper()
                if "MO" in txt:
                    wl = 0.71073
                elif "CU" in txt:
                    wl = 1.54178
                elif "AG" in txt:
                    wl = 0.56086
            elif key in ("SPTS", "SPGRP", "SG") and sg is None:
                sg = " ".join(tok[1:]).strip()
    return {"cell": cell, "wavelength": wl or 0.71073, "sg": sg}


def parse_ins(path: str) -> dict:
    cell = None
    wl = None
    sfac = []
    latt = None
    symm = []
    with open(path) as f:
        for line in f:
            tok = line.split()
            if not tok:
                continue
            key = tok[0].upper()
            if key == "CELL" and len(tok) >= 8:
                wl = float(tok[1])
                cell = [float(x) for x in tok[2:8]]
            elif key == "SFAC":
                sfac = [x.capitalize() for x in tok[1:]]
            elif key == "LATT":
                latt = int(tok[1])
            elif key == "SYMM":
                symm.append(" ".join(tok[1:]))
    return {
        "cell": cell,
        "wavelength": wl or 0.71073,
        "elements": sfac,
        "latt": latt,
        "symm_ops_text": symm,
    }


# ═══════════════════════════════════════════════════════════════════════
# Space group operations
# ═══════════════════════════════════════════════════════════════════════
_SG_OPS: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {
    1: [(np.eye(3), np.zeros(3))],
    2: [(np.eye(3), np.zeros(3)), (-np.eye(3), np.zeros(3))],
    4: [  # P 2₁
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0])),
    ],
    5: [  # C 2 (unique axis b, C-centred)
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.zeros(3)),
        (np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0])),
    ],
    9: [  # C c (unique axis b)
        (np.eye(3), np.zeros(3)),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0, 0.5])),
        (np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
    ],
    14: [  # P 2₁/c
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0.5, 0.5])),
    ],
    15: [  # C 2/c (unique axis b)
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0, 0.5])),
        (np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0.5])),
        (-np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
    ],
    18: [  # P 21 21 2
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0])),
    ],
    19: [  # P 2₁ 2₁ 2₁
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0.5])),
    ],
    33: [  # P n a 21  (ITA: x,y,z; -x,-y,z+½; -x+½,y+½,z+½; x+½,-y+½,z)
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0, 0, 0.5])),
        (np.diag([-1.0, 1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0])),
    ],
    61: [  # P b c a  (ITA: x,y,z; -x+½,-y,z+½; -x,y+½,-z+½; x+½,-y+½,-z; + inversion)
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, 1.0, -1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0.5, 0.5])),
        (np.diag([-1.0, 1.0, 1.0]), np.array([0.5, 0.5, 0])),
    ],
    62: [  # P n m a  (ITA: x,y,z; -x+½,-y,z+½; -x,y+½,-z; x+½,-y+½,-z+½; + inversion)
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, 1.0, -1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0.5, 0])),
        (np.diag([-1.0, 1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
    ],
}
_SG_NAME_MAP = {
    "P1": 1,
    "P-1": 2,
    "P21": 4,
    "P 21": 4,
    "P 2_1": 4,
    "P2₁": 4,
    "C2": 5,
    "C 2": 5,
    "Cc": 9,
    "C c": 9,
    "P21/c": 14,
    "P 21/c": 14,
    "P2₁/c": 14,
    "P 2_1/c": 14,
    "P21/n": 14,
    "P 21/n": 14,
    "C2/c": 15,
    "C 2/c": 15,
    "P21212": 18,
    "P 21 21 2": 18,
    "P212121": 19,
    "P 21 21 21": 19,
    "Pna21": 33,
    "P n a 21": 33,
    "Pna2_1": 33,
    "Pbca": 61,
    "P b c a": 61,
    "Pnma": 62,
    "P n m a": 62,
}


def _get_sg_ops(sg_input) -> tuple[list, int]:
    """Return (ops_list, sg_number). Each op = (R_3x3, t_3)."""
    if sg_input is None:
        return _SG_OPS[1], 1
    # Try as int
    try:
        n = int(sg_input)
        if n in _SG_OPS:
            return _SG_OPS[n], n
    except (ValueError, TypeError):
        pass
    # Normalise name
    name = str(sg_input).strip().replace("_", "")
    for key, n in _SG_NAME_MAP.items():
        if name.replace(" ", "").lower() == key.replace(" ", "").lower():
            if n in _SG_OPS:
                return _SG_OPS[n], n
    # Try pymatgen
    try:
        from pymatgen.symmetry.groups import SpaceGroup

        sg = SpaceGroup(sg_input)
        ops = [
            (op.rotation_matrix.astype(float), op.translation_vector.astype(float))
            for op in sg.symmetry_ops
        ]
        return ops, sg.int_number
    except Exception:
        pass
    print(f"⚠ Unknown space group '{sg_input}'; defaulting to P1", file=sys.stderr)
    return _SG_OPS[1], 1


# ═══════════════════════════════════════════════════════════════════════
# Charge-flipping structure solution
# ═══════════════════════════════════════════════════════════════════════
def _charge_flipping(
    hkl, f_obs, cell, sg_ops, grid=96, cycles=800, delta_frac=0.85, n_trials=3
):
    """Run charge flipping and return the best electron-density map."""
    N = grid
    h, k, ell = hkl[:, 0], hkl[:, 1], hkl[:, 2]
    hi, ki, li = h % N, k % N, ell % N
    hf, kf, lf = (-h) % N, (-k) % N, (-ell) % N

    # Precompute symmetry index maps for averaging
    sg_maps = []
    if len(sg_ops) > 1:
        idx = np.arange(N)
        for R, t in sg_ops[1:]:
            mi, mj, mk = [], [], []
            for dim in range(3):
                frac = np.zeros(N, dtype=float)
                for d2 in range(3):
                    if R[dim, d2] != 0:
                        frac += R[dim, d2] * idx / N
                frac += t[dim]
                mapped = np.round(frac * N).astype(int) % N
                if dim == 0:
                    mi = mapped
                elif dim == 1:
                    mj = mapped
                else:
                    mk = mapped
            sg_maps.append((mi, mj, mk))

    best_rho = None
    best_r = float("inf")

    for _ in range(n_trials):
        phases = np.random.uniform(0, 2 * np.pi, len(f_obs))
        for _ in range(cycles):
            F_grid = np.zeros((N, N, N), dtype=complex)
            F = f_obs * np.exp(1j * phases)
            np.add.at(F_grid, (hi, ki, li), F)
            np.add.at(F_grid, (hf, kf, lf), F.conjugate())

            rho = np.real(np.fft.ifftn(F_grid))

            # Symmetry averaging
            if sg_maps:
                rho_sum = rho.copy()
                for mi, mj, mk in sg_maps:
                    rho_sum += rho[np.ix_(mi, mj, mk)]
                rho = rho_sum / len(sg_ops)

            # Charge flip
            sigma = np.std(rho)
            delta = delta_frac * sigma
            rho = np.where(rho < delta, -rho, rho)

            F_new = np.fft.fftn(rho)
            for i in range(len(f_obs)):
                phases[i] = np.angle(F_new[hi[i], ki[i], li[i]])

        # Compute final density with latest phases
        F_grid = np.zeros((N, N, N), dtype=complex)
        F = f_obs * np.exp(1j * phases)
        np.add.at(F_grid, (hi, ki, li), F)
        np.add.at(F_grid, (hf, kf, lf), F.conjugate())
        rho_final = np.real(np.fft.ifftn(F_grid))

        # Quick R-factor
        f_calc = np.abs(F_grid[hi, ki, li])
        r = np.sum(np.abs(f_obs - f_calc)) / np.sum(f_obs) if np.sum(f_obs) > 0 else 1
        if r < best_r:
            best_r = r
            best_rho = rho_final

    return best_rho


# ═══════════════════════════════════════════════════════════════════════
# Atom finding
# ═══════════════════════════════════════════════════════════════════════
def _find_atoms(rho, cell, sg_ops, sigma_thresh=4.5, min_dist_A=0.8):
    """Locate atoms as peaks in the electron density."""
    from scipy.ndimage import maximum_filter

    N = rho.shape[0]
    sigma = np.std(rho)
    threshold = sigma_thresh * sigma
    local_max = maximum_filter(rho, size=3)
    mask = (rho == local_max) & (rho > threshold)
    coords = np.argwhere(mask)
    vals = rho[mask]
    order = np.argsort(-vals)
    coords = coords[order]
    vals = vals[order]
    frac = coords / N
    a, b, c = cell[0], cell[1], cell[2]

    keep_idx = []
    kept_frac: list[np.ndarray] = []
    for i in range(len(frac)):
        duplicate = False
        for xk in kept_frac:
            for R, t in sg_ops:
                equiv = (R @ xk + t) % 1.0
                diff = frac[i] - equiv
                diff -= np.round(diff)
                dist = np.sqrt(
                    (diff[0] * a) ** 2 + (diff[1] * b) ** 2 + (diff[2] * c) ** 2
                )
                if dist < min_dist_A:
                    duplicate = True
                    break
            if duplicate:
                break
        if not duplicate:
            keep_idx.append(i)
            kept_frac.append(frac[i])
    return np.array(kept_frac), vals[keep_idx]


def _assign_types(peak_vals, elements=None):
    """Guess atom types from peak heights."""
    Z = {
        "H": 1,
        "C": 6,
        "N": 7,
        "O": 8,
        "F": 9,
        "Si": 14,
        "P": 15,
        "S": 16,
        "Cl": 17,
        "Br": 35,
        "I": 53,
        "Fe": 26,
        "Cu": 29,
        "Zn": 30,
    }
    if elements is None:
        elements = ["C", "N", "O", "S", "Cl", "Br"]
    max_Z = max(Z.get(e, 6) for e in elements)
    max_val = peak_vals[0] if len(peak_vals) else 1
    types = []
    for v in peak_vals:
        est = v / max_val * max_Z
        best = min(elements, key=lambda e: abs(Z.get(e, 6) - est))
        types.append(best)
    return types


# ═══════════════════════════════════════════════════════════════════════
# Least-squares refinement on F²
# ═══════════════════════════════════════════════════════════════════════
def _calc_f(atoms, hkl, cell, wavelength, sg_ops):
    """F_calc for all reflections, given atom list of dicts."""
    s_sq = _d_star_sq(hkl, cell) / 4.0  # (sinθ/λ)²
    F = np.zeros(len(hkl), dtype=complex)
    h, k, ell = (
        hkl[:, 0].astype(float),
        hkl[:, 1].astype(float),
        hkl[:, 2].astype(float),
    )
    for at in atoms:
        x, y, z = at["frac"]
        el = at["elem"]
        B = at.get("B", 2.0)
        f_el = _scatt(el, s_sq) * np.exp(-B * s_sq)
        for R, t in sg_ops:
            xn = R[0, 0] * x + R[0, 1] * y + R[0, 2] * z + t[0]
            yn = R[1, 0] * x + R[1, 1] * y + R[1, 2] * z + t[1]
            zn = R[2, 0] * x + R[2, 1] * y + R[2, 2] * z + t[2]
            phase = 2 * np.pi * (h * xn + k * yn + ell * zn)
            F += f_el * np.exp(1j * phase)
    return F


def _refine(atoms, hkl_data, cell, wavelength, sg_ops, max_iter=5):
    """Refine positions + iso-B on F². Returns (refined_atoms, r_dict)."""
    hkl = hkl_data["hkl"]
    fsq_obs = hkl_data["fsq"]
    sigma = hkl_data["sigma"]
    # Use only observed reflections with positive F²
    sel = fsq_obs > 0
    hkl_sel = hkl[sel]
    fsq_sel = fsq_obs[sel]
    sig_sel = np.maximum(sigma[sel], 1.0)
    w = 1.0 / sig_sel

    def _pack(atms, scale):
        v = [scale]
        for a in atms:
            v.extend(a["frac"])
            v.append(a.get("B", 2.0))
        return np.array(v, dtype=float)

    def _unpack(v):
        sc = v[0]
        atms = []
        idx = 1
        for a in atoms:
            atms.append(
                {
                    "elem": a["elem"],
                    "frac": v[idx : idx + 3].tolist(),
                    "B": max(0.5, v[idx + 3]),
                }
            )
            idx += 4
        return sc, atms

    # Initial scale
    Fc0 = _calc_f(atoms, hkl_sel, cell, wavelength, sg_ops)
    sum_o = np.sum(fsq_sel)
    sum_c = np.sum(np.abs(Fc0) ** 2)
    scale0 = sum_o / sum_c if sum_c > 0 else 1.0

    p0 = _pack(atoms, scale0)

    def _res(p):
        sc, atms = _unpack(p)
        Fc = _calc_f(atms, hkl_sel, cell, wavelength, sg_ops)
        return w * (fsq_sel - sc * np.abs(Fc) ** 2)

    result = least_squares(_res, p0, method="lm", max_nfev=max_iter * len(p0))
    sc_fin, atoms_fin = _unpack(result.x)

    # R-factors
    Fc_fin = _calc_f(atoms_fin, hkl_sel, cell, wavelength, sg_ops)
    fsq_calc = sc_fin * np.abs(Fc_fin) ** 2
    fo = np.sqrt(np.maximum(fsq_sel, 0))
    fc = np.sqrt(np.maximum(fsq_calc, 0))
    R1 = float(np.sum(np.abs(fo - fc)) / np.sum(fo)) if np.sum(fo) > 0 else 1.0
    wR2 = (
        float(
            np.sqrt(
                np.sum((w * (fsq_sel - fsq_calc)) ** 2) / np.sum((w * fsq_sel) ** 2)
            )
        )
        if np.sum(fsq_sel) > 0
        else 1.0
    )
    n_par = len(result.x)
    n_obs = len(fsq_sel)
    goof = float(np.sqrt(np.sum(result.fun**2) / max(n_obs - n_par, 1)))

    return atoms_fin, {
        "R1": round(R1, 4),
        "wR2": round(wR2, 4),
        "GOOF": round(goof, 3),
        "n_obs": n_obs,
        "n_params": n_par,
        "scale": round(float(sc_fin), 6),
    }


# ═══════════════════════════════════════════════════════════════════════
# CIF writer
# ═══════════════════════════════════════════════════════════════════════
def _write_cif(
    path, cell, sg_symbol, sg_number, atoms, rfactors, wavelength, formula_str="?"
):
    V = round(_cell_volume(cell), 2)
    a, b, c, al, be, ga = cell
    lines = [
        "data_structure",
        f"_cell_length_a                    {a:.4f}",
        f"_cell_length_b                    {b:.4f}",
        f"_cell_length_c                    {c:.4f}",
        f"_cell_angle_alpha                 {al:.2f}",
        f"_cell_angle_beta                  {be:.2f}",
        f"_cell_angle_gamma                 {ga:.2f}",
        f"_cell_volume                      {V}",
        f"_space_group_name_H-M_alt         '{sg_symbol}'",
        f"_space_group_IT_number            {sg_number}",
        f"_chemical_formula_sum             '{formula_str}'",
        f"_diffrn_radiation_wavelength      {wavelength:.5f}",
        f"_refine_ls_R_factor_gt            {rfactors['R1']:.4f}",
        f"_refine_ls_wR_factor_ref          {rfactors['wR2']:.4f}",
        f"_refine_ls_goodness_of_fit_ref    {rfactors['GOOF']:.3f}",
        f"_refine_ls_number_reflns          {rfactors['n_obs']}",
        f"_refine_ls_number_parameters      {rfactors['n_params']}",
        "",
        "loop_",
        " _atom_site_label",
        " _atom_site_type_symbol",
        " _atom_site_fract_x",
        " _atom_site_fract_y",
        " _atom_site_fract_z",
        " _atom_site_U_iso_or_equiv",
        " _atom_site_adp_type",
    ]
    elem_count: dict[str, int] = {}
    for at in atoms:
        el = at["elem"]
        elem_count[el] = elem_count.get(el, 0) + 1
        label = f"{el}{elem_count[el]}"
        x, y, z = at["frac"]
        U = at.get("B", 2.0) / (8 * np.pi**2)
        lines.append(
            f" {label:6s} {el:2s}  {x:10.5f} {y:10.5f} {z:10.5f}  {U:8.5f} Uiso"
        )

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return V


def _formula_from_atoms(atoms, sg_ops):
    """Build approximate formula string."""
    counts: dict[str, int] = {}
    Z = len(sg_ops)  # multiplicity
    for at in atoms:
        el = at["elem"]
        counts[el] = counts.get(el, 0) + 1
    parts = []
    for el in ("C", "H", "N", "O"):
        if el in counts:
            n = counts.pop(el) * Z
            parts.append(f"{el}{n}" if n > 1 else el)
    for el in sorted(counts):
        n = counts[el] * Z
        parts.append(f"{el}{n}" if n > 1 else el)
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# SHELX integration (optional — preferred when available)
# ═══════════════════════════════════════════════════════════════════════
def _try_shelx(hkl_path, cell, wavelength, sg_number, elements, prefix="struct"):
    """Attempt SHELX pipeline. Returns CIF path on success, else None."""
    shelxs = (
        shutil.which("shelxs")
        or shutil.which("shelxs-2018")
        or shutil.which("shelxs-97")
    )
    shelxl = (
        shutil.which("shelxl")
        or shutil.which("shelxl-2018")
        or shutil.which("shelxl-97")
    )
    if not shelxs or not shelxl:
        return None

    a, b, c, al, be, ga = cell
    # LATT: negative = acentric, positive = centric
    # C-centred space groups use |LATT| = 7; P-lattice uses |LATT| = 1
    _LATT_MAP = {
        1: -1, 2: 1, 4: -1, 5: -7, 9: -7, 14: 1, 15: 7,
        18: -1, 19: -1, 33: -1, 61: 1, 62: 1,
    }
    _Z_MAP = {
        1: 1, 2: 2, 4: 2, 5: 4, 9: 4, 14: 4, 15: 8,
        18: 4, 19: 4, 33: 4, 61: 8, 62: 8,
    }
    latt_sign = _LATT_MAP.get(sg_number, -1)
    Z = _Z_MAP.get(sg_number, 4)

    ins_lines = [
        f"TITL auto_{prefix}",
        f"CELL {wavelength:.5f} {a:.4f} {b:.4f} {c:.4f} {al:.2f} {be:.2f} {ga:.2f}",
        f"ZERR {Z} 0.001 0.001 0.001 0.01 0.01 0.01",
        f"LATT {latt_sign}",
    ]
    # Symmetry operations for common space groups
    _SYMM = {
        4: ["SYMM -X, 0.5+Y, -Z"],
        5: ["SYMM -X, Y, -Z"],
        9: ["SYMM X, -Y, 0.5+Z"],
        14: ["SYMM -X, 0.5+Y, 0.5-Z"],
        15: ["SYMM -X, Y, 0.5-Z"],
        18: ["SYMM -X, -Y, Z", "SYMM 0.5-X, 0.5+Y, -Z", "SYMM 0.5+X, 0.5-Y, -Z"],
        19: ["SYMM 0.5-X, -Y, 0.5+Z", "SYMM 0.5+X, 0.5-Y, -Z", "SYMM -X, 0.5+Y, 0.5-Z"],
        33: ["SYMM -X, -Y, 0.5+Z", "SYMM 0.5-X, 0.5+Y, 0.5+Z", "SYMM 0.5+X, 0.5-Y, Z"],
        61: ["SYMM 0.5-X, -Y, 0.5+Z", "SYMM -X, 0.5+Y, 0.5-Z", "SYMM 0.5+X, 0.5-Y, -Z"],
        62: ["SYMM 0.5-X, -Y, 0.5+Z", "SYMM -X, 0.5+Y, -Z", "SYMM 0.5+X, 0.5-Y, 0.5-Z"],
    }
    for symm_line in _SYMM.get(sg_number, []):
        ins_lines.append(symm_line)

    elems = elements or ["C", "H", "N", "O"]
    ins_lines.append("SFAC " + " ".join(elems))
    ins_lines.append("UNIT " + " ".join(str(Z * 10) for _ in elems))
    ins_lines.append("TREF")
    ins_lines.append("HKLF 4")
    ins_lines.append("END")

    ins_path = Path(f"{prefix}.ins")
    ins_path.write_text("\n".join(ins_lines) + "\n")
    shutil.copy(hkl_path, f"{prefix}.hkl")

    try:
        subprocess.run([shelxs, prefix], capture_output=True, timeout=120)
    except Exception:
        return None

    res_path = Path(f"{prefix}.res")
    if not res_path.exists():
        return None

    # Prepare refinement INS from RES
    shutil.copy(res_path, ins_path)
    try:
        subprocess.run([shelxl, prefix], capture_output=True, timeout=300)
    except Exception:
        return None

    cif_path = Path(f"{prefix}.cif")
    return str(cif_path) if cif_path.exists() else None


# ═══════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="SCXRD structure solution & CIF generation"
    )
    ap.add_argument("--hkl", required=True, help="HKL file (SHELX HKLF4)")
    ap.add_argument("--p4p", help="Bruker P4P file")
    ap.add_argument("--ins", help="SHELX INS file (alternative to P4P)")
    ap.add_argument("--cell", help='Manual cell "a b c alpha beta gamma"')
    ap.add_argument("--sg", help="Space group symbol or number")
    ap.add_argument("--wavelength", type=float, help="Wavelength in Å")
    ap.add_argument("--elements", help='Expected elements, e.g. "C H N O S"')
    ap.add_argument(
        "--grid", type=int, default=96, help="Charge-flipping grid (default 96)"
    )
    ap.add_argument("--cycles", type=int, default=800, help="CF cycles (default 800)")
    ap.add_argument(
        "--trials", type=int, default=3, help="CF random trials (default 3)"
    )
    ap.add_argument("-o", "--output", default="refined.cif", help="Output CIF path")
    args = ap.parse_args()

    # ── Gather cell, wavelength, space group ──
    cell = wl = sg_str = None
    elements = None

    if args.p4p:
        info = parse_p4p(args.p4p)
        cell, wl, sg_str = info["cell"], info["wavelength"], info.get("sg")
    if args.ins:
        info = parse_ins(args.ins)
        cell = cell or info["cell"]
        wl = wl or info["wavelength"]
        elements = info.get("elements") or None
    if args.cell:
        cell = [float(x) for x in args.cell.split()]
    if args.sg:
        sg_str = args.sg
    if args.wavelength:
        wl = args.wavelength
    if args.elements:
        elements = args.elements.split()
    wl = wl or 0.71073

    if cell is None:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "No cell parameters found. Provide --p4p, --ins, or --cell.",
                }
            )
        )
        sys.exit(1)

    sg_ops, sg_number = _get_sg_ops(sg_str)
    sg_symbol = sg_str or f"P (#{sg_number})"

    print(f"Cell: {cell}", file=sys.stderr)
    print(f"SG: {sg_symbol} (#{sg_number}), wavelength: {wl} Å", file=sys.stderr)

    # ── Read reflections ──
    hkl_data = parse_hkl(args.hkl)
    n_ref = len(hkl_data["fsq"])
    print(f"Reflections: {n_ref}", file=sys.stderr)

    # ── Try SHELX first ──
    shelx_cif = _try_shelx(args.hkl, cell, wl, sg_number, elements)
    if shelx_cif:
        shutil.copy(shelx_cif, args.output)
        print(
            json.dumps(
                {
                    "success": True,
                    "method": "SHELX",
                    "cif": args.output,
                    "cell_volume": round(_cell_volume(cell), 2),
                    "space_group": sg_symbol,
                    "space_group_number": sg_number,
                },
                indent=2,
            )
        )
        return

    print("SHELX not available; using Python charge-flipping…", file=sys.stderr)

    # ── Charge flipping ──
    f_obs = np.sqrt(np.maximum(hkl_data["fsq"], 0))
    rho = _charge_flipping(
        hkl_data["hkl"],
        f_obs,
        cell,
        sg_ops,
        grid=args.grid,
        cycles=args.cycles,
        n_trials=args.trials,
    )

    # ── Find atoms ──
    frac_coords, peak_vals = _find_atoms(rho, cell, sg_ops)
    if len(frac_coords) == 0:
        print(
            json.dumps(
                {"success": False, "error": "No atoms found in charge-flipping density"}
            )
        )
        sys.exit(1)

    # Limit to reasonable number (avoid noise peaks)
    V = _cell_volume(cell)
    max_atoms = max(int(V / 10), 60)  # ~10 ų per atom
    frac_coords = frac_coords[:max_atoms]
    peak_vals = peak_vals[:max_atoms]

    types = _assign_types(peak_vals, elements)
    atoms = [
        {"elem": t, "frac": list(fc), "B": 2.0} for t, fc in zip(types, frac_coords)
    ]
    print(f"Atoms found: {len(atoms)}", file=sys.stderr)

    # ── Refine ──
    try:
        atoms_ref, rfactors = _refine(atoms, hkl_data, cell, wl, sg_ops, max_iter=8)
    except Exception as e:
        print(f"Refinement error: {e}; writing unrefined CIF", file=sys.stderr)
        atoms_ref = atoms
        rfactors = {
            "R1": 0.99,
            "wR2": 0.99,
            "GOOF": 0.0,
            "n_obs": n_ref,
            "n_params": 1 + 4 * len(atoms),
            "scale": 1.0,
        }

    # ── Write CIF ──
    formula = _formula_from_atoms(atoms_ref, sg_ops)
    vol = _write_cif(
        args.output, cell, sg_symbol, sg_number, atoms_ref, rfactors, wl, formula
    )

    summary = {
        "success": True,
        "method": "charge_flipping",
        "cif": args.output,
        "cell_volume": vol,
        "space_group": sg_symbol,
        "space_group_number": sg_number,
        "R1": rfactors["R1"],
        "wR2": rfactors["wR2"],
        "GOOF": rfactors["GOOF"],
        "n_atoms_asym": len(atoms_ref),
        "formula": formula,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
