"""
solve_refine_scxrd_lib.py — Helper library for SCXRD structure solution & refinement.

Provides:
  - Cromer-Mann atomic scattering factor coefficients (37 elements)
  - Space group symmetry operations (12 common groups)
  - Charge-flipping algorithm with early convergence detection
  - Atom-finding from electron density maps
  - Crystal system detection, symop<->xyz conversion, molecular weight
"""

import numpy as np

# ---------------------------------------------------------------------------
# Cromer-Mann scattering factor coefficients  (a1 b1 a2 b2 a3 b3 a4 b4 c)
# f(s) = sum_i a_i exp(-b_i s^2) + c   where s = sin(theta)/lambda
# ---------------------------------------------------------------------------
CROMER_MANN: dict[str, tuple[float, ...]] = {
    "H": (
        0.489918,
        20.6593,
        0.262003,
        7.74039,
        0.196767,
        49.5519,
        0.049879,
        2.20159,
        0.001305,
    ),
    "He": (0.8734, 9.1037, 0.6309, 3.3568, 0.3112, 22.9276, 0.1780, 0.9821, 0.0064),
    "Li": (1.1282, 3.9546, 0.7508, 1.0524, 0.6175, 85.3905, 0.4653, 168.261, 0.0377),
    "Be": (1.5919, 43.6427, 1.1278, 1.8623, 0.5391, 103.483, 0.7029, 0.5420, 0.0385),
    "B": (2.0545, 23.2185, 1.3326, 1.0210, 1.0979, 60.3498, 0.7068, 0.1403, -0.1932),
    "C": (2.3100, 20.8439, 1.0200, 10.2075, 1.5886, 0.5687, 0.8650, 51.6512, 0.2156),
    "N": (12.2126, 0.0057, 3.1322, 9.8933, 2.0125, 28.9975, 1.1663, 0.5826, -11.529),
    "O": (3.0485, 13.2771, 2.2868, 5.7011, 1.5463, 0.3239, 0.8670, 32.9089, 0.2508),
    "F": (3.5392, 10.2825, 2.6412, 4.2944, 1.5170, 0.2615, 1.0243, 26.1476, 0.2776),
    "Na": (4.7626, 3.2850, 3.1736, 8.8422, 1.2674, 0.3136, 1.1128, 129.424, 0.6760),
    "Mg": (5.4204, 2.8275, 2.1735, 79.2611, 1.2269, 0.3808, 2.3073, 7.1937, 0.8584),
    "Al": (6.4202, 3.0387, 1.9002, 0.7426, 1.5936, 31.5472, 1.9646, 85.0886, 1.1151),
    "Si": (6.2915, 2.4386, 3.0353, 32.3337, 1.9891, 0.6785, 1.5410, 81.6937, 1.1407),
    "P": (6.4345, 1.9067, 4.1791, 27.157, 1.7800, 0.526, 1.4908, 68.1645, 1.1149),
    "S": (6.9053, 1.4679, 5.2034, 22.2151, 1.4379, 0.2536, 1.5863, 56.172, 0.8669),
    "Cl": (11.4604, 0.0104, 7.1964, 1.1662, 6.2556, 18.5194, 1.6455, 47.7784, -9.5574),
    "K": (8.2186, 12.7949, 7.4398, 0.7748, 1.0519, 213.187, 0.8659, 41.6841, 1.4228),
    "Ca": (8.6266, 10.4421, 7.3873, 0.6599, 1.5899, 85.7484, 1.0211, 178.437, 1.3751),
    "Ti": (9.7595, 7.8508, 7.3558, 0.5000, 1.6991, 35.6338, 1.9021, 116.105, 1.2807),
    "V": (10.2971, 6.8657, 7.3511, 0.4385, 2.0703, 26.8938, 2.0571, 102.478, 1.2199),
    "Cr": (10.6406, 6.1038, 7.3537, 0.3920, 3.3240, 20.2626, 1.4922, 98.7399, 1.1832),
    "Mn": (11.2819, 5.3409, 7.3573, 0.3432, 3.0193, 17.8674, 2.2441, 83.7543, 1.0896),
    "Fe": (11.7695, 4.7611, 7.3573, 0.3072, 3.5222, 15.3535, 2.3045, 76.8805, 1.0369),
    "Co": (12.2841, 4.2791, 7.3409, 0.2784, 4.0034, 13.5359, 2.3488, 71.1692, 1.0118),
    "Ni": (12.8376, 3.8785, 7.2920, 0.2565, 4.4438, 12.1763, 2.3800, 66.3421, 1.0341),
    "Cu": (13.338, 3.5828, 7.1676, 0.2470, 5.6158, 11.3966, 1.6735, 64.8126, 1.1910),
    "Zn": (14.0743, 3.2655, 7.0318, 0.2333, 5.1652, 10.3163, 2.4100, 58.7097, 1.3041),
    "Ga": (15.2354, 3.0669, 6.7006, 0.2412, 4.3591, 10.7805, 2.9623, 61.4135, 1.7189),
    "Ge": (16.0816, 2.8509, 6.3747, 0.2516, 3.7068, 11.4468, 3.683, 54.7625, 2.1313),
    "As": (16.6723, 2.6345, 6.0701, 0.2647, 3.4313, 12.9479, 4.2779, 47.7972, 2.531),
    "Se": (17.0006, 2.4098, 5.8196, 0.2726, 3.9731, 15.2372, 4.3543, 43.8163, 2.8409),
    "Br": (17.1789, 2.1723, 5.2358, 16.5796, 5.6377, 0.2609, 3.9851, 41.4328, 2.9557),
    "Rb": (17.5816, 1.7139, 7.6598, 14.7957, 5.8981, 0.1603, 2.7817, 31.2087, 2.0782),
    "Sr": (17.5663, 1.5564, 9.8184, 14.0988, 5.422, 0.1664, 2.6694, 132.376, 2.5064),
    "Zr": (17.8765, 1.2762, 10.948, 11.916, 5.4173, 0.117647, 3.6571, 87.6627, 2.0693),
    "I": (20.1472, 4.347, 18.9949, 0.3814, 7.5138, 27.766, 2.2735, 66.8776, 4.0712),
    "Ba": (20.3361, 3.216, 19.297, 0.2756, 10.888, 20.2073, 2.6959, 167.202, 7.1364),
}

# Molecular weights (g/mol) for common elements
ATOMIC_WEIGHTS: dict[str, float] = {
    "H": 1.008,
    "He": 4.003,
    "Li": 6.941,
    "Be": 9.012,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "Ne": 20.180,
    "Na": 22.990,
    "Mg": 24.305,
    "Al": 26.982,
    "Si": 28.086,
    "P": 30.974,
    "S": 32.065,
    "Cl": 35.453,
    "Ar": 39.948,
    "K": 39.098,
    "Ca": 40.078,
    "Sc": 44.956,
    "Ti": 47.867,
    "V": 50.942,
    "Cr": 51.996,
    "Mn": 54.938,
    "Fe": 55.845,
    "Co": 58.933,
    "Ni": 58.693,
    "Cu": 63.546,
    "Zn": 65.38,
    "Ga": 69.723,
    "Ge": 72.64,
    "As": 74.922,
    "Se": 78.96,
    "Br": 79.904,
    "Rb": 85.468,
    "Sr": 87.62,
    "Y": 88.906,
    "Zr": 91.224,
    "Mo": 95.96,
    "Ag": 107.868,
    "Cd": 112.411,
    "Sn": 118.710,
    "Sb": 121.760,
    "I": 126.904,
    "Ba": 137.327,
    "La": 138.905,
    "Ce": 140.116,
    "W": 183.84,
    "Pt": 195.084,
    "Au": 196.967,
    "Pb": 207.2,
    "Bi": 208.980,
}

# ---------------------------------------------------------------------------
# Space group symmetry operations
# Each entry:  { "number": int, "symbol": str, "crystal_system": str,
#                "ops": [ ((R_3x3), (t_3,)) , ... ] }
# R is a 3×3 integer rotation, t is a fractional translation (mod 1)
# ---------------------------------------------------------------------------


def _op(r: list[list[int]], t: list[float] = None):
    """Helper to build (rotation_matrix, translation_vector)."""
    t = t or [0.0, 0.0, 0.0]
    return (np.array(r, dtype=float), np.array(t, dtype=float))


_I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
_nI = [[-1, 0, 0], [0, -1, 0], [0, 0, -1]]

SPACE_GROUPS: dict[str, dict] = {
    # --- Triclinic ---
    "P1": {
        "number": 1,
        "symbol": "P1",
        "crystal_system": "triclinic",
        "ops": [_op(_I)],
    },
    "P-1": {
        "number": 2,
        "symbol": "P-1",
        "crystal_system": "triclinic",
        "ops": [_op(_I), _op(_nI)],
    },
    # --- Monoclinic ---
    "P2_1": {
        "number": 4,
        "symbol": "P2_1",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
        ],
    },
    "P21": {  # alias
        "number": 4,
        "symbol": "P2_1",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
        ],
    },
    "P2_1/m": {
        "number": 11,
        "symbol": "P2_1/m",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
            _op(_nI),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0]),
        ],
    },
    "P21/m": {  # alias
        "number": 11,
        "symbol": "P2_1/m",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
            _op(_nI),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0]),
        ],
    },
    "C2": {
        "number": 5,
        "symbol": "C2",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]]),
            # C-centering translations
            _op(_I, [0.5, 0.5, 0]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "Cc": {
        "number": 9,
        "symbol": "Cc",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0, 0.5]),
            _op(_I, [0.5, 0.5, 0]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0.5, 0.5]),
        ],
    },
    "P2_1/c": {
        "number": 14,
        "symbol": "P2_1/c",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
            _op(_nI),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0.5]),
        ],
    },
    "P21/c": {  # alias
        "number": 14,
        "symbol": "P2_1/c",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
            _op(_nI),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0.5]),
        ],
    },
    "C2/c": {
        "number": 15,
        "symbol": "C2/c",
        "crystal_system": "monoclinic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0, 0.5]),
            _op(_nI),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0, 0.5]),
            # C-centering
            _op(_I, [0.5, 0.5, 0]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0.5, 0.5]),
            _op(_nI, [0.5, 0.5, 0]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0.5, 0.5]),
        ],
    },
    # --- Orthorhombic ---
    "P2_12_12": {
        "number": 18,
        "symbol": "P2_12_12",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "P212121": {
        "number": 19,
        "symbol": "P2_12_12_1",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "P2_12_12_1": {
        "number": 19,
        "symbol": "P2_12_12_1",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "Pna2_1": {
        "number": 33,
        "symbol": "Pna2_1",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "Pna21": {  # alias
        "number": 33,
        "symbol": "Pna2_1",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
        ],
    },
    "Pbca": {
        "number": 61,
        "symbol": "Pbca",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0]),
            _op(_nI),
            _op([[1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.5, 0.5, 0]),
        ],
    },
    "Pnma": {
        "number": 62,
        "symbol": "Pnma",
        "crystal_system": "orthorhombic",
        "ops": [
            _op(_I),
            _op([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [0.5, 0, 0.5]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], [0, 0.5, 0]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.5, 0.5, 0.5]),
            _op(_nI),
            _op([[1, 0, 0], [0, 1, 0], [0, 0, -1]], [0.5, 0, 0.5]),
            _op([[1, 0, 0], [0, -1, 0], [0, 0, 1]], [0, 0.5, 0]),
            _op([[-1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.5, 0.5, 0.5]),
        ],
    },
}

# Convenient alias map for lookup
SG_ALIASES: dict[str, str] = {
    "P 1": "P1",
    "P -1": "P-1",
    "P 2(1)": "P2_1",
    "P 21": "P21",
    "P 2(1)/c": "P2_1/c",
    "P 21/c": "P21/c",
    "P21/c": "P21/c",
    "C 2": "C2",
    "P2_1/m": "P2_1/m",
    "P 2(1)/m": "P2_1/m",
    "P 21/m": "P21/m",
    "P21/m": "P21/m",
    "C c": "Cc",
    "C 2/c": "C2/c",
    "P 2(1) 2(1) 2": "P2_12_12",
    "P 21 21 2": "P2_12_12",
    "P 2(1) 2(1) 2(1)": "P2_12_12_1",
    "P 21 21 21": "P212121",
    "P n a 2(1)": "Pna2_1",
    "P n a 21": "Pna21",
    "Pna 21": "Pna21",
    "P b c a": "Pbca",
    "P n m a": "Pnma",
}


def lookup_sg(name: str) -> dict | None:
    """Look up a space group by name (with alias resolution)."""
    name = name.strip()
    # Direct lookup
    if name in SPACE_GROUPS:
        return SPACE_GROUPS[name]
    # Alias
    key = SG_ALIASES.get(name)
    if key and key in SPACE_GROUPS:
        return SPACE_GROUPS[key]
    # Try removing spaces
    compact = name.replace(" ", "")
    if compact in SPACE_GROUPS:
        return SPACE_GROUPS[compact]
    return None


def get_crystal_system(sg_name: str) -> str:
    """Return the crystal system for a space group name."""
    sg = lookup_sg(sg_name)
    if sg:
        return sg["crystal_system"]
    return "unknown"


# ---------------------------------------------------------------------------
# Structure factor calculation
# ---------------------------------------------------------------------------


def calc_f_calc(
    hkl: np.ndarray, atoms: list[dict], cell: dict, sg_ops: list[tuple]
) -> np.ndarray:
    """
    Calculate structure factors F_calc for an array of (h,k,l).

    atoms: list of {"element": str, "x": float, "y": float, "z": float, "uiso": float, "occ": float}
    cell: {"a","b","c","alpha","beta","gamma"} in Å / degrees
    sg_ops: list of (R_3x3, t_3) from SPACE_GROUPS

    Returns complex F_calc array of shape (N_refl,).
    """
    a, b, c = cell["a"], cell["b"], cell["c"]
    al = np.radians(cell["alpha"])
    be = np.radians(cell["beta"])
    ga = np.radians(cell["gamma"])

    # Reciprocal metric tensor components for d-spacing
    cos_al, cos_be, cos_ga = np.cos(al), np.cos(be), np.cos(ga)
    sin_al, sin_be, sin_ga = np.sin(al), np.sin(be), np.sin(ga)

    vol = (
        a
        * b
        * c
        * np.sqrt(1 - cos_al**2 - cos_be**2 - cos_ga**2 + 2 * cos_al * cos_be * cos_ga)
    )

    # Reciprocal cell
    ar = b * c * sin_al / vol
    br = a * c * sin_be / vol
    cr = a * b * sin_ga / vol

    cos_al_r = (cos_be * cos_ga - cos_al) / (sin_be * sin_ga)
    cos_be_r = (cos_al * cos_ga - cos_be) / (sin_al * sin_ga)
    cos_ga_r = (cos_al * cos_be - cos_ga) / (sin_al * sin_be)

    h = hkl[:, 0]
    k = hkl[:, 1]
    l_idx = hkl[:, 2]

    # 1/d^2
    d_star_sq = (
        (h * ar) ** 2
        + (k * br) ** 2
        + (l_idx * cr) ** 2
        + 2 * h * k * ar * br * cos_ga_r
        + 2 * h * l_idx * ar * cr * cos_be_r
        + 2 * k * l_idx * br * cr * cos_al_r
    )
    s_sq = d_star_sq / 4.0  # (sin(theta)/lambda)^2

    F = np.zeros(len(hkl), dtype=complex)

    for atom in atoms:
        elem = atom["element"]
        x, y, z = atom["x"], atom["y"], atom["z"]
        uiso = atom.get("uiso", 0.05)
        occ = atom.get("occ", 1.0)

        # Scattering factor
        cm = CROMER_MANN.get(elem)
        if cm is None:
            # Fallback: use carbon
            cm = CROMER_MANN["C"]
        a1, b1, a2, b2, a3, b3, a4, b4, cc = cm
        f0 = (
            a1 * np.exp(-b1 * s_sq)
            + a2 * np.exp(-b2 * s_sq)
            + a3 * np.exp(-b3 * s_sq)
            + a4 * np.exp(-b4 * s_sq)
            + cc
        )

        # Debye-Waller
        dw = np.exp(-8 * np.pi**2 * uiso * s_sq)

        # Sum over symmetry operations
        for R, t in sg_ops:
            pos = R @ np.array([x, y, z]) + t
            phase = 2 * np.pi * (h * pos[0] + k * pos[1] + l_idx * pos[2])
            F += occ * f0 * dw * np.exp(1j * phase)

    return F


# ---------------------------------------------------------------------------
# Charge-flipping algorithm
# ---------------------------------------------------------------------------


def charge_flipping(
    hkl: np.ndarray,
    f_obs: np.ndarray,
    grid_size: int = 72,
    n_trials: int = 2,
    n_cycles: int = 400,
    delta_frac: float = 0.15,
    convergence_window: int = 20,
    convergence_threshold: float = 0.002,
) -> tuple[np.ndarray, float]:
    """
    Charge-flipping structure solution.

    Parameters
    ----------
    hkl : (N, 3) int array of Miller indices
    f_obs : (N,) float array of observed |F|
    grid_size : density grid per axis
    n_trials : number of random-phase trials
    n_cycles : max iterations per trial
    delta_frac : flip threshold as fraction of rho std
    convergence_window : cycles to check convergence
    convergence_threshold : R-factor change threshold for early stop

    Returns
    -------
    best_rho : (grid_size, grid_size, grid_size) electron density
    best_r : best R-factor achieved
    """
    N = grid_size
    best_rho = None
    best_r = 1.0

    for trial in range(n_trials):
        # Random initial phases
        rng = np.random.RandomState(42 + trial)
        phases = rng.uniform(0, 2 * np.pi, len(f_obs))
        F = f_obs * np.exp(1j * phases)

        # Build density grid from F
        rho = np.zeros((N, N, N), dtype=complex)
        r_history = []

        for _cycle in range(n_cycles):
            # Place F into grid
            rho[:] = 0
            for i, (hh, kk, ll) in enumerate(hkl):
                hi = int(hh) % N
                ki = int(kk) % N
                li = int(ll) % N
                rho[hi, ki, li] = F[i]

            # Inverse FFT to get density
            rho_real = np.real(np.fft.ifftn(rho)) * N**3

            # Flip low-density voxels
            sigma = np.std(rho_real)
            delta = delta_frac * sigma
            mask = rho_real < delta
            rho_real[mask] = -rho_real[mask]

            # Forward FFT
            rho_k = np.fft.fftn(rho_real) / N**3

            # Extract new phases, keep |F_obs|
            F_new = np.zeros(len(hkl), dtype=complex)
            for i, (hh, kk, ll) in enumerate(hkl):
                hi = int(hh) % N
                ki = int(kk) % N
                li = int(ll) % N
                F_new[i] = rho_k[hi, ki, li]

            new_phases = np.angle(F_new)
            F = f_obs * np.exp(1j * new_phases)

            # R-factor
            f_calc_mag = np.abs(F_new)
            scale = np.sum(f_obs * f_calc_mag) / (np.sum(f_calc_mag**2) + 1e-12)
            r = np.sum(np.abs(f_obs - scale * f_calc_mag)) / (np.sum(f_obs) + 1e-12)
            r_history.append(r)

            # Early convergence
            if len(r_history) >= convergence_window:
                recent = r_history[-convergence_window:]
                if max(recent) - min(recent) < convergence_threshold:
                    break

        # Final density
        rho[:] = 0
        for i, (hh, kk, ll) in enumerate(hkl):
            hi = int(hh) % N
            ki = int(kk) % N
            li = int(ll) % N
            rho[hi, ki, li] = F[i]
        final_rho = np.real(np.fft.ifftn(rho)) * N**3

        final_r = r_history[-1] if r_history else 1.0
        if final_r < best_r:
            best_r = final_r
            best_rho = final_rho.copy()

    return best_rho, best_r


def find_atoms_from_density(
    rho: np.ndarray,
    cell: dict,
    elements: list[str],
    sigma_thresh: float = 3.5,
    min_dist: float = 0.8,
) -> list[dict]:
    """
    Find atomic positions from an electron density map.

    Parameters
    ----------
    rho : 3D density array
    cell : unit cell dict
    elements : list of element symbols expected
    sigma_thresh : peak detection threshold in sigma units
    min_dist : minimum inter-atomic distance (Å)

    Returns list of {"element", "x", "y", "z"} in fractional coords.
    """
    N = rho.shape[0]
    mean_rho = np.mean(rho)
    std_rho = np.std(rho)
    threshold = mean_rho + sigma_thresh * std_rho

    # Find peaks above threshold
    peaks = []
    for i in range(N):
        for j in range(N):
            for k in range(N):
                if rho[i, j, k] > threshold:
                    # Check if local maximum (6-connected)
                    val = rho[i, j, k]
                    is_max = True
                    for di, dj, dk in [
                        (1, 0, 0),
                        (-1, 0, 0),
                        (0, 1, 0),
                        (0, -1, 0),
                        (0, 0, 1),
                        (0, 0, -1),
                    ]:
                        ni, nj, nk = (i + di) % N, (j + dj) % N, (k + dk) % N
                        if rho[ni, nj, nk] > val:
                            is_max = False
                            break
                    if is_max:
                        peaks.append((val, i / N, j / N, k / N))

    # Sort by density (highest first)
    peaks.sort(key=lambda p: -p[0])

    # Cell parameters for distance calculation
    a, b, c = cell["a"], cell["b"], cell["c"]

    # Remove peaks too close to each other
    filtered = []
    for val, fx, fy, fz in peaks:
        too_close = False
        for _, ex, ey, ez in filtered:
            dx = (fx - ex) % 1.0
            if dx > 0.5:
                dx -= 1.0
            dy = (fy - ey) % 1.0
            if dy > 0.5:
                dy -= 1.0
            dz = (fz - ez) % 1.0
            if dz > 0.5:
                dz -= 1.0
            dist = np.sqrt((dx * a) ** 2 + (dy * b) ** 2 + (dz * c) ** 2)
            if dist < min_dist:
                too_close = True
                break
        if not too_close:
            filtered.append((val, fx, fy, fz))

    # Assign elements by density (heaviest → highest density)
    # Sort elements by atomic number (proxy for scattering power)
    elem_weights = [(ATOMIC_WEIGHTS.get(e, 12.0), e) for e in elements]
    elem_weights.sort(key=lambda x: -x[0])

    atoms = []
    # Count how many of each element we expect (simple: distribute evenly)
    # In practice, we assign heaviest elements to strongest peaks
    for idx, (_val, fx, fy, fz) in enumerate(filtered):
        if idx < len(elements):
            # Assign from sorted elements
            elem = elem_weights[idx % len(elem_weights)][1]
        else:
            # Extra peaks: use lightest element
            elem = elem_weights[-1][1]
        atoms.append({"element": elem, "x": fx, "y": fy, "z": fz})

    return atoms


# ---------------------------------------------------------------------------
# Symmetry operation <-> string conversion
# ---------------------------------------------------------------------------


def symop_to_xyz(R: np.ndarray, t: np.ndarray) -> str:
    """Convert (R, t) to CIF xyz string like 'x, y, z' or '-x, y+1/2, -z+1/2'."""
    labels = ["x", "y", "z"]
    parts = []
    for i in range(3):
        terms = []
        for j in range(3):
            c = int(round(R[i, j]))
            if c == 1:
                terms.append(f"+{labels[j]}")
            elif c == -1:
                terms.append(f"-{labels[j]}")
        frac = t[i] % 1.0
        if frac > 0.001:
            # Convert to fraction string
            if abs(frac - 0.5) < 0.01:
                terms.append("+1/2")
            elif abs(frac - 1.0 / 3) < 0.01:
                terms.append("+1/3")
            elif abs(frac - 2.0 / 3) < 0.01:
                terms.append("+2/3")
            elif abs(frac - 0.25) < 0.01:
                terms.append("+1/4")
            elif abs(frac - 0.75) < 0.01:
                terms.append("+3/4")
            else:
                terms.append(f"+{frac:.4f}")
        s = "".join(terms)
        if s.startswith("+"):
            s = s[1:]
        parts.append(s)
    return ", ".join(parts)


def molecular_weight(elements: list[str]) -> float:
    """Sum of atomic weights for given element list."""
    return sum(ATOMIC_WEIGHTS.get(e, 12.0) for e in elements)


def cell_volume(
    a: float, b: float, c: float, alpha: float, beta: float, gamma: float
) -> float:
    """Unit cell volume from parameters (lengths Å, angles degrees)."""
    al = np.radians(alpha)
    be = np.radians(beta)
    ga = np.radians(gamma)
    return (
        a
        * b
        * c
        * np.sqrt(
            1
            - np.cos(al) ** 2
            - np.cos(be) ** 2
            - np.cos(ga) ** 2
            + 2 * np.cos(al) * np.cos(be) * np.cos(ga)
        )
    )


def hill_formula(elements: list[str]) -> str:
    """
    Return molecular formula in Hill order (C first, then H, then alphabetical).
    """
    from collections import Counter

    counts = Counter(elements)
    parts = []
    # Hill: C first, H second, then alpha
    for e in ["C", "H"]:
        if e in counts:
            n = counts.pop(e)
            parts.append(f"{e}{n if n > 1 else ''}")
    for e in sorted(counts.keys()):
        n = counts[e]
        parts.append(f"{e}{n if n > 1 else ''}")
    return " ".join(parts) if parts else "?"
