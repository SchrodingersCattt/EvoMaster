"""SCXRD pipeline implementation: I/O, symmetry, charge-flipping, refinement, CIF, SHELX."""

from __future__ import annotations

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
    "Si": ([6.2915, 3.0353, 1.9891, 1.5410], [2.439, 32.334, 0.679, 81.694], 1.1407),
    "P": ([6.4345, 4.1791, 1.7800, 1.4908], [1.907, 27.157, 0.526, 68.165], 1.1149),
    "S": ([6.9053, 5.2034, 1.4379, 1.5863], [1.468, 22.215, 0.254, 56.172], 0.8669),
    "Cl": ([11.460, 7.1964, 6.2556, 1.6455], [0.010, 1.166, 18.519, 47.778], -9.5574),
    "Br": ([17.179, 5.2358, 5.6377, 3.9851], [2.172, 16.580, 0.261, 41.433], 2.9557),
    "I": ([20.147, 18.995, 7.5138, 2.2735], [4.347, 0.381, 27.766, 66.878], 4.0712),
    "Fe": ([11.770, 7.3573, 3.5222, 2.3045], [4.761, 0.307, 15.353, 76.881], 1.0369),
    "Cu": ([13.338, 7.1676, 5.6158, 1.6735], [3.583, 0.247, 11.397, 64.812], 1.1910),
    "Zn": ([14.074, 7.0318, 5.1652, 2.4100], [3.266, 0.233, 10.316, 58.710], 1.3041),
    "Na": ([4.7626, 3.1736, 1.2674, 1.1128], [3.285, 8.842, 0.314, 129.424], 0.6760),
    "Mg": ([5.4204, 2.1735, 1.2269, 2.3073], [2.828, 79.261, 0.381, 7.194], 0.8584),
    "Al": ([6.4202, 1.9002, 1.5936, 1.9646], [3.039, 0.743, 31.547, 85.089], 1.1151),
    "K": ([8.2186, 7.4398, 1.0519, 0.8659], [12.795, 0.775, 213.187, 41.684], 1.4228),
    "Ca": ([8.6266, 7.3873, 1.5899, 1.0211], [10.442, 0.660, 85.748, 178.437], 1.3751),
    "Ti": ([9.7595, 7.3558, 1.6991, 1.9021], [7.851, 0.500, 35.634, 116.105], 1.2807),
    "Mn": ([11.282, 7.3573, 3.0193, 2.2441], [5.341, 0.343, 17.867, 83.754], 1.0896),
    "Co": ([12.284, 7.3409, 4.0034, 2.3488], [4.279, 0.278, 13.536, 71.169], 1.0118),
    "Ni": ([12.838, 7.2920, 4.4438, 2.3800], [3.878, 0.257, 12.176, 66.342], 1.0341),
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
    5: [  # C 2
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.zeros(3)),
        (np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0])),
    ],
    14: [  # P 2₁/c
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0.5, 0.5])),
    ],
    15: [  # C 2/c
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, -1.0, 1.0]), np.array([0, 0, 0.5])),
        (np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0.5])),
        (-np.eye(3), np.array([0.5, 0.5, 0])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
    ],
    19: [  # P 2₁ 2₁ 2₁
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0, 0.5, 0.5])),
    ],
    33: [  # P n a 2₁
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0, 0, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0.5, 0.5, 0.5])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0])),
    ],
    61: [  # P b c a
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0, 0.5, 0.5])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, 1.0, -1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([-1.0, 1.0, 1.0]), np.array([0, 0.5, 0.5])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0])),
    ],
    62: [  # P n m a
        (np.eye(3), np.zeros(3)),
        (np.diag([-1.0, -1.0, 1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([1.0, -1.0, -1.0]), np.array([0, 0.5, 0])),
        (np.diag([-1.0, 1.0, -1.0]), np.array([0.5, 0.5, 0.5])),
        (-np.eye(3), np.zeros(3)),
        (np.diag([1.0, 1.0, -1.0]), np.array([0.5, 0, 0.5])),
        (np.diag([-1.0, 1.0, 1.0]), np.array([0, 0.5, 0])),
        (np.diag([1.0, -1.0, 1.0]), np.array([0.5, 0.5, 0.5])),
    ],
}
_SG_NAME_MAP = {
    "P1": 1,
    "P 1": 1,
    "P-1": 2,
    "P -1": 2,
    "P21": 4,
    "P 21": 4,
    "P 2_1": 4,
    "P2₁": 4,
    "C2": 5,
    "C 2": 5,
    "P21/c": 14,
    "P 21/c": 14,
    "P2₁/c": 14,
    "P 2_1/c": 14,
    "P21/n": 14,
    "P 21/n": 14,
    "P2₁/n": 14,
    "C2/c": 15,
    "C 2/c": 15,
    "P212121": 19,
    "P 21 21 21": 19,
    "Pna21": 33,
    "P n a 21": 33,
    "Pna2₁": 33,
    "P 21/a": 14,
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

    # Use deterministic seed for reproducibility across evaluation runs.
    # The seed incorporates the number of reflections and grid size to
    # still produce different starting phases for different datasets.
    rng = np.random.default_rng(seed=42 + len(f_obs) + N)

    for _ in range(n_trials):
        phases = rng.uniform(0, 2 * np.pi, len(f_obs))
        for _cf_iter in range(cycles):
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

            # Vectorized phase extraction (replaces O(N) Python loop)
            F_new = np.fft.fftn(rho)
            new_phases = np.angle(F_new[hi, ki, li])

            # Early convergence: stop if phases stabilise (circular mean diff)
            if _cf_iter > 30:
                phase_diff = np.mod(new_phases - phases + np.pi, 2 * np.pi) - np.pi
                if np.mean(np.abs(phase_diff)) < 0.02:
                    phases = new_phases
                    break
            phases = new_phases

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
        "Na": 11,
        "Mg": 12,
        "Al": 13,
        "Si": 14,
        "P": 15,
        "S": 16,
        "Cl": 17,
        "K": 19,
        "Ca": 20,
        "Ti": 22,
        "Mn": 25,
        "Fe": 26,
        "Co": 27,
        "Ni": 28,
        "Cu": 29,
        "Zn": 30,
        "Br": 35,
        "I": 53,
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
def _crystal_system(sg_number: int) -> str:
    """Return the crystal system string from space group number."""
    if sg_number <= 2:
        return "triclinic"
    elif sg_number <= 15:
        return "monoclinic"
    elif sg_number <= 74:
        return "orthorhombic"
    elif sg_number <= 142:
        return "tetragonal"
    elif sg_number <= 167:
        return "trigonal"
    elif sg_number <= 194:
        return "hexagonal"
    else:
        return "cubic"


def _molecular_weight(formula_str: str) -> float:
    """Estimate molecular weight from formula string like 'C10 H12 N2 O3'."""
    _AW = {
        "H": 1.008,
        "C": 12.011,
        "N": 14.007,
        "O": 15.999,
        "F": 18.998,
        "Na": 22.990,
        "Mg": 24.305,
        "Al": 26.982,
        "Si": 28.086,
        "P": 30.974,
        "S": 32.065,
        "Cl": 35.453,
        "K": 39.098,
        "Ca": 40.078,
        "Ti": 47.867,
        "Mn": 54.938,
        "Fe": 55.845,
        "Co": 58.933,
        "Ni": 58.693,
        "Cu": 63.546,
        "Zn": 65.38,
        "Br": 79.904,
        "I": 126.904,
    }
    import re as _re

    mw = 0.0
    for match in _re.finditer(r"([A-Z][a-z]?)(\d*)", formula_str):
        el = match.group(1)
        n = int(match.group(2)) if match.group(2) else 1
        mw += _AW.get(el, 12.0) * n
    return round(mw, 2)


def _symop_xyz(R, t):
    """Convert rotation matrix R and translation t to x,y,z string notation."""
    axes = ["x", "y", "z"]
    parts = []
    for i in range(3):
        terms = []
        for j in range(3):
            coeff = R[i, j]
            if abs(coeff) < 1e-8:
                continue
            sign = "+" if coeff > 0 else "-"
            ac = abs(coeff)
            if abs(ac - 1.0) < 1e-8:
                terms.append(f"{sign}{axes[j]}")
            elif abs(ac - 0.5) < 1e-8:
                terms.append(f"{sign}1/2*{axes[j]}")
            else:
                terms.append(f"{sign}{ac:.0f}*{axes[j]}")
        # Translation component
        ti = t[i] % 1.0
        if ti > 0.999:
            ti = 0.0
        if ti > 1e-4:
            # Express as fraction
            frac_map = {
                0.5: "1/2",
                0.25: "1/4",
                0.75: "3/4",
                1 / 3: "1/3",
                2 / 3: "2/3",
                1 / 6: "1/6",
                5 / 6: "5/6",
            }
            found = False
            for fv, fs in frac_map.items():
                if abs(ti - fv) < 1e-4:
                    terms.append(f"+{fs}")
                    found = True
                    break
            if not found:
                terms.append(f"+{ti:.4f}")
        comp = "".join(terms)
        # Clean up leading +
        if comp.startswith("+"):
            comp = comp[1:]
        if not comp:
            comp = "0"
        parts.append(comp)
    return ",".join(parts)


def _write_cif(
    path,
    cell,
    sg_symbol,
    sg_number,
    atoms,
    rfactors,
    wavelength,
    formula_str="?",
    z_formula=None,
    sg_ops=None,
):
    V = round(_cell_volume(cell), 2)
    a, b, c, al, be, ga = cell
    # Use caller-supplied Z (= len(sg_ops)) when available; fall back to
    # the small built-in table only as a last resort.
    Z = (
        z_formula
        if z_formula is not None
        else len(_SG_OPS.get(sg_number, [(None, None)]))
    )
    if Z < 1:
        Z = 1
    cryst_sys = _crystal_system(sg_number)
    mw = _molecular_weight(formula_str) if formula_str != "?" else 0.0

    # Calculate crystal density (g/cm³)
    # density = Z * M / (V * N_A) where V in ų = 1e-24 cm³
    density_str = "?"
    if mw > 0 and V > 0:
        density = (Z * mw) / (V * 0.6022)  # 0.6022 = N_A * 1e-24
        density_str = f"{density:.3f}"

    lines = [
        "data_structure",
        "",
        "# Audit",
        "_audit_creation_method            'solve_refine_scxrd.py (charge-flipping + LS)'",
        "",
        "# Crystal data",
        f"_cell_length_a                    {a:.4f}",
        f"_cell_length_b                    {b:.4f}",
        f"_cell_length_c                    {c:.4f}",
        f"_cell_angle_alpha                 {al:.2f}",
        f"_cell_angle_beta                  {be:.2f}",
        f"_cell_angle_gamma                 {ga:.2f}",
        f"_cell_volume                      {V}",
        f"_cell_formula_units_Z             {Z}",
        "",
        "# Space group",
        f"_space_group_name_H-M_alt         '{sg_symbol}'",
        f"_space_group_IT_number            {sg_number}",
        f"_space_group_crystal_system       {cryst_sys}",
        f"_symmetry_cell_setting            {cryst_sys}",
        f"_symmetry_space_group_name_H-M    '{sg_symbol}'",
        f"_symmetry_Int_Tables_number       {sg_number}",
        "",
    ]
    # Symmetry operations loop (required for CIF compliance)
    ops_to_write = (
        sg_ops
        if sg_ops is not None
        else _SG_OPS.get(sg_number, [(np.eye(3), np.zeros(3))])
    )
    lines.append("loop_")
    lines.append(" _symmetry_equiv_pos_site_id")
    lines.append(" _symmetry_equiv_pos_as_xyz")
    for idx, (R, t) in enumerate(ops_to_write, start=1):
        xyz_str = _symop_xyz(R, t)
        lines.append(f" {idx} '{xyz_str}'")
    lines.append("")
    lines += [
        "# Chemical information",
        f"_chemical_formula_sum             '{formula_str}'",
        f"_chemical_formula_moiety          '{formula_str}'",
    ]
    if mw > 0:
        lines.append(f"_chemical_formula_weight           {mw}")
    if density_str != "?":
        lines.append(f"_exptl_crystal_density_diffrn      {density_str}")
    lines += [
        "",
        "# Data collection",
        f"_diffrn_radiation_wavelength      {wavelength:.5f}",
        "",
        "# Refinement statistics",
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
        " _atom_site_occupancy",
    ]
    elem_count: dict[str, int] = {}
    for at in atoms:
        el = at["elem"]
        elem_count[el] = elem_count.get(el, 0) + 1
        label = f"{el}{elem_count[el]}"
        x, y, z = at["frac"]
        # Wrap fractional coordinates into [0, 1)
        x, y, z = x % 1.0, y % 1.0, z % 1.0
        U = at.get("B", 2.0) / (8 * np.pi**2)
        occ = at.get("occ", 1.0)
        lines.append(
            f" {label:6s} {el:2s}  {x:10.5f} {y:10.5f} {z:10.5f}  {U:8.5f} Uiso  {occ:.4f}"
        )

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return V


def _formula_from_atoms(atoms, sg_ops):
    """Build approximate formula string (per-formula-unit = asymmetric unit).

    CIF convention: ``_chemical_formula_sum`` is per formula unit;
    ``_cell_formula_units_Z`` tells how many formula units fill the cell.
    Since the *atoms* list is the asymmetric unit (independent atoms before
    symmetry expansion), we report those counts directly — do NOT multiply
    by Z (the space-group multiplicity).

    Hill order: C first, H second, then all remaining elements alphabetically.
    """
    counts: dict[str, int] = {}
    for at in atoms:
        el = at["elem"]
        counts[el] = counts.get(el, 0) + 1
    # Hill order: C first, H second, then ALL others alphabetically
    parts = []
    if "C" in counts:
        n = counts.pop("C")
        parts.append(f"C{n}" if n > 1 else "C")
    if "H" in counts:
        n = counts.pop("H")
        parts.append(f"H{n}" if n > 1 else "H")
    # Remaining elements in strict alphabetical order
    for el in sorted(counts):
        n = counts[el]
        parts.append(f"{el}{n}" if n > 1 else el)
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Difference Fourier synthesis — find missing atoms from residual density
# ═══════════════════════════════════════════════════════════════════════
def _diff_fourier_atoms(
    atoms, hkl_data, cell, wavelength, sg_ops, scale,
    grid=96, sigma_thresh=3.0, min_dist_A=1.0, max_new=20,
):
    """Compute difference Fourier (Fo-Fc) map and find new atom peaks.

    Returns list of new atom fractional coordinates and peak heights,
    excluding positions already occupied by *atoms* or their symmetry
    equivalents.
    """
    from scipy.ndimage import maximum_filter

    hkl = hkl_data["hkl"]
    fsq_obs = hkl_data["fsq"]
    sel = fsq_obs > 0
    hkl_sel = hkl[sel]
    fsq_sel = fsq_obs[sel]

    # Compute F_calc to get phases
    Fc = _calc_f(atoms, hkl_sel, cell, wavelength, sg_ops)
    Fc_scaled = np.sqrt(scale) * Fc  # scale is on F² so sqrt for F
    phi_calc = np.angle(Fc_scaled)

    # Difference amplitudes: |Fo| - |Fc|
    fo = np.sqrt(np.maximum(fsq_sel, 0))
    fc = np.abs(Fc_scaled)
    delta_f = fo - fc

    # Build difference Fourier map
    N = grid
    h, k, ell = hkl_sel[:, 0], hkl_sel[:, 1], hkl_sel[:, 2]
    hi, ki, li = h % N, k % N, ell % N
    hf, kf, lf = (-h) % N, (-k) % N, (-ell) % N

    F_diff = delta_f * np.exp(1j * phi_calc)
    F_grid = np.zeros((N, N, N), dtype=complex)
    np.add.at(F_grid, (hi, ki, li), F_diff)
    np.add.at(F_grid, (hf, kf, lf), F_diff.conjugate())
    rho_diff = np.real(np.fft.ifftn(F_grid))

    # Find peaks in difference density
    sigma = np.std(rho_diff)
    threshold = sigma_thresh * sigma
    local_max = maximum_filter(rho_diff, size=3)
    mask = (rho_diff == local_max) & (rho_diff > threshold)
    coords = np.argwhere(mask)
    vals = rho_diff[mask]
    order = np.argsort(-vals)
    coords = coords[order]
    vals = vals[order]
    frac = coords / N
    a_len, b_len, c_len = cell[0], cell[1], cell[2]

    # Filter: not too close to existing atoms or their symmetry equivalents
    existing_frac = [np.array(at["frac"]) for at in atoms]
    new_frac = []
    new_vals = []
    for i in range(min(len(frac), max_new * 3)):
        too_close = False
        for xk in existing_frac + new_frac:
            for R, t in sg_ops:
                equiv = (R @ xk + t) % 1.0
                diff = frac[i] - equiv
                diff -= np.round(diff)
                dist = np.sqrt(
                    (diff[0] * a_len) ** 2
                    + (diff[1] * b_len) ** 2
                    + (diff[2] * c_len) ** 2
                )
                if dist < min_dist_A:
                    too_close = True
                    break
            if too_close:
                break
        if not too_close:
            new_frac.append(frac[i])
            new_vals.append(vals[i])
            if len(new_frac) >= max_new:
                break
    return new_frac, new_vals


def _iterative_solve(
    rho, hkl_data, cell, wavelength, sg_ops, elements=None,
    grid=96, max_diff_cycles=5, verbose=True,
):
    """Iterative structure solution: charge-flip peaks → refine → ΔF → add atoms → repeat.

    Returns (atoms, rfactors) with significantly improved R-factors compared
    to the single-pass pipeline.
    """
    V = _cell_volume(cell)
    max_atoms = max(int(V / 10), 60)

    # ── Initial atom finding with lower threshold to catch more atoms ──
    frac_coords, peak_vals = _find_atoms(rho, cell, sg_ops, sigma_thresh=3.5)
    if len(frac_coords) == 0:
        return [], {"R1": 0.99, "wR2": 0.99, "GOOF": 0.0,
                     "n_obs": 0, "n_params": 0, "scale": 1.0}

    frac_coords = frac_coords[:max_atoms]
    peak_vals = peak_vals[:max_atoms]
    types = _assign_types(peak_vals, elements)
    atoms = [
        {"elem": t, "frac": list(fc), "B": 2.0}
        for t, fc in zip(types, frac_coords)
    ]
    if verbose:
        print(f"Initial atoms from charge-flipping: {len(atoms)}", file=sys.stderr)

    # ── Iterative: refine → difference Fourier → add atoms → re-refine ──
    best_r1 = 1.0
    best_atoms = atoms
    best_rfactors = None

    for cycle in range(max_diff_cycles):
        # Refine current model
        try:
            atoms_ref, rfactors = _refine(
                atoms, hkl_data, cell, wavelength, sg_ops, max_iter=8
            )
        except Exception as e:
            if verbose:
                print(f"Refinement cycle {cycle} error: {e}", file=sys.stderr)
            break

        r1 = rfactors["R1"]
        if verbose:
            print(
                f"Cycle {cycle}: {len(atoms_ref)} atoms, R1={r1:.4f}",
                file=sys.stderr,
            )

        if r1 < best_r1:
            best_r1 = r1
            best_atoms = atoms_ref
            best_rfactors = rfactors

        # If R1 is already good enough, stop early
        if r1 < 0.10:
            break

        # Difference Fourier to find missing atoms
        new_frac, new_vals = _diff_fourier_atoms(
            atoms_ref, hkl_data, cell, wavelength, sg_ops,
            scale=rfactors["scale"], grid=grid,
            sigma_thresh=2.5 if cycle > 1 else 3.0,
            min_dist_A=1.0,
            max_new=max(5, max_atoms - len(atoms_ref)),
        )

        if not new_frac:
            if verbose:
                print(f"No new atoms found in ΔF map at cycle {cycle}", file=sys.stderr)
            break

        # Assign types to new atoms
        if new_vals:
            new_types = _assign_types(
                np.array(new_vals),
                [e for e in (elements or ["C", "N", "O"]) if e != "H"],
            )
        else:
            new_types = []

        # Build extended atom list
        atoms = list(atoms_ref)
        added = 0
        for nf, nt in zip(new_frac, new_types):
            if len(atoms) >= max_atoms:
                break
            atoms.append({"elem": nt, "frac": list(nf), "B": 3.0})
            added += 1

        if verbose:
            print(f"  Added {added} atoms from ΔF map", file=sys.stderr)
        if added == 0:
            break

    # Final refinement with all atoms (more iterations)
    if best_atoms and len(best_atoms) > 0:
        try:
            final_atoms, final_rf = _refine(
                best_atoms, hkl_data, cell, wavelength, sg_ops, max_iter=15
            )
            if final_rf["R1"] < best_r1:
                best_atoms = final_atoms
                best_rfactors = final_rf
                if verbose:
                    print(
                        f"Final refinement: {len(final_atoms)} atoms, R1={final_rf['R1']:.4f}",
                        file=sys.stderr,
                    )
        except Exception:
            pass

    if best_rfactors is None:
        best_rfactors = {
            "R1": 0.99, "wR2": 0.99, "GOOF": 0.0,
            "n_obs": len(hkl_data["fsq"]), "n_params": 1, "scale": 1.0,
        }

    return best_atoms, best_rfactors


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
    latt_sign = -1  # acentric
    Z = 2 if sg_number == 4 else 4

    ins_lines = [
        f"TITL auto_{prefix}",
        f"CELL {wavelength:.5f} {a:.4f} {b:.4f} {c:.4f} {al:.2f} {be:.2f} {ga:.2f}",
        f"ZERR {Z} 0.001 0.001 0.001 0.01 0.01 0.01",
        f"LATT {latt_sign}",
    ]
    # Symmetry operations for common space groups
    if sg_number == 4:
        ins_lines.append("SYMM -X, 0.5+Y, -Z")
    elif sg_number == 14:
        ins_lines.append("SYMM -X, 0.5+Y, 0.5-Z")
    elif sg_number == 19:
        ins_lines += [
            "SYMM 0.5-X, -Y, 0.5+Z",
            "SYMM 0.5+X, 0.5-Y, -Z",
            "SYMM -X, 0.5+Y, 0.5-Z",
        ]

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
