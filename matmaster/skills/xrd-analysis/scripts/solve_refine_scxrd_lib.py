#!/usr/bin/env python3
"""
solve_refine_scxrd_lib.py — Helper library for SCXRD structure solution.

Provides:
- Cromer-Mann X-ray scattering factors (37 elements)
- Space group symmetry operations (12 common groups)
- Charge-flipping algorithm
- Atom finding from electron density maps
- Utility functions (crystal system, molecular weight, etc.)
"""

import numpy as np
from typing import List, Tuple, Dict, Optional

# ---------------------------------------------------------------------------
# Cromer-Mann scattering factor coefficients: {element: (a1,b1,a2,b2,a3,b3,a4,b4,c)}
# From International Tables for Crystallography, Vol C
# ---------------------------------------------------------------------------
SCATTERING_FACTORS: Dict[str, Tuple[float, ...]] = {
    "H":  (0.493002, 10.5109, 0.322912, 26.1257, 0.140191, 3.14236, 0.040810, 57.7997, 0.003038),
    "C":  (2.31000, 20.8439, 1.02000, 10.2075, 1.58860, 0.56870, 0.865000, 51.6512, 0.21560),
    "N":  (12.2126, 0.00570, 3.13220, 9.89330, 2.01250, 28.9975, 1.16630, 0.58260, -11.529),
    "O":  (3.04850, 13.2771, 2.28680, 5.70110, 1.54630, 0.32390, 0.867000, 32.9089, 0.25080),
    "F":  (3.53920, 10.2825, 2.64120, 4.29440, 1.51700, 0.26150, 1.02430, 26.1476, 0.27760),
    "Na": (4.76260, 3.28500, 3.17360, 8.84220, 1.26740, 0.31360, 1.11280, 129.424, 0.67600),
    "Mg": (5.42040, 2.82750, 2.17350, 79.2611, 1.22690, 0.38080, 2.30730, 7.19370, 0.85840),
    "Al": (6.42020, 3.03870, 1.90020, 0.74260, 1.59360, 31.5472, 1.96460, 85.0886, 1.11510),
    "Si": (6.29150, 2.43860, 3.03530, 32.3337, 1.98910, 0.67850, 1.54100, 81.6937, 1.14070),
    "P":  (6.43450, 1.90670, 4.17910, 27.1570, 1.78000, 0.52600, 1.49080, 68.1645, 1.11490),
    "S":  (6.90530, 1.46790, 5.20340, 22.2151, 1.43790, 0.25360, 1.58630, 56.1720, 0.86690),
    "Cl": (11.4604, 0.01040, 7.19640, 1.16620, 6.25560, 18.5194, 1.64550, 47.7784, -9.5574),
    "K":  (8.21860, 12.7949, 7.43980, 0.77480, 1.05190, 213.187, 0.86590, 41.6841, 1.42280),
    "Ca": (8.62660, 10.4421, 7.38730, 0.65990, 1.58990, 85.7484, 1.02110, 178.437, 1.37510),
    "Ti": (9.75950, 7.85080, 7.35580, 0.50000, 1.69910, 35.6338, 1.90210, 116.105, 1.28070),
    "Mn": (11.2819, 5.34090, 7.35730, 0.34320, 3.01930, 17.8674, 2.24410, 83.7543, 1.08960),
    "Fe": (11.7695, 4.76110, 7.35730, 0.30720, 3.52220, 15.3535, 2.30450, 76.8805, 1.03690),
    "Co": (12.2841, 4.27910, 7.34090, 0.27840, 4.00340, 13.5359, 2.34880, 71.1692, 1.01180),
    "Ni": (12.8376, 3.87850, 7.29200, 0.25650, 4.44380, 12.1763, 2.38000, 66.3421, 1.03410),
    "Cu": (13.3380, 3.58280, 7.16760, 0.24700, 5.61580, 11.3966, 1.67350, 64.8126, 1.19100),
    "Zn": (14.0743, 3.26550, 7.03180, 0.23330, 5.16520, 10.3163, 2.41000, 58.7097, 1.30410),
    "Ga": (15.2354, 3.06690, 6.70060, 0.24120, 4.35910, 10.7805, 2.96230, 61.4135, 1.71890),
    "Ge": (16.0816, 2.85090, 6.37470, 0.25160, 3.70680, 11.4468, 3.68300, 54.7625, 2.13130),
    "As": (16.6723, 2.63450, 6.07010, 0.26470, 3.43130, 12.9479, 4.27790, 47.7972, 2.53100),
    "Se": (17.0006, 2.40980, 5.81960, 0.27260, 3.97310, 15.2372, 4.35430, 43.8163, 2.84090),
    "Mo": (3.70250, 0.27720, 17.2356, 1.09580, 12.8876, 11.0040, 3.74290, 61.6584, 4.38750),
    "Ag": (19.2808, 0.64460, 16.6885, 7.47260, 4.80450, 24.6605, 1.04630, 99.8156, 5.17900),
    "Cd": (19.2214, 0.59460, 17.6444, 6.90890, 4.46100, 24.7008, 1.60290, 87.4825, 5.06940),
    "Sn": (19.1889, 5.83030, 19.1005, 0.50310, 4.45850, 26.8909, 2.46630, 83.9571, 4.78210),
    "Ba": (20.3361, 3.21600, 19.2970, 0.27560, 10.8882, 20.2073, 2.69590, 167.202, 2.77310),
    "W":  (29.0818, 1.72029, 15.4300, 9.22590, 14.4327, 0.32170, 5.11982, 57.0560, -0.09760),
    "Pt": (27.0059, 1.51293, 17.7639, 8.81174, 15.7131, 0.42459, 5.78370, 38.6103, 11.6883),
    "Au": (16.8819, 0.46110, 18.5913, 8.62160, 25.5582, 1.48260, 5.86000, 36.3956, 12.0658),
    "Pb": (31.0617, 0.69020, 13.0637, 2.35760, 18.4420, 8.61800, 5.96960, 47.2579, 13.4118),
    "Bi": (33.3689, 0.70400, 12.9510, 2.92380, 16.5877, 8.79370, 6.46920, 48.0093, 13.5782),
    "Br": (17.1789, 2.17230, 5.23580, 16.5796, 5.63770, 0.26090, 3.98510, 41.4328, 2.95570),
    "I":  (20.1472, 4.34700, 18.9949, 0.38140, 7.51380, 27.7660, 2.27350, 66.8776, 4.07120),
}

# ---------------------------------------------------------------------------
# Space group symmetry operations
# Each entry: (number, symbol, [list of 3x4 augmented matrices])
# Matrix format: [[r11,r12,r13,t1],[r21,r22,r23,t2],[r31,r32,r33,t3]]
# ---------------------------------------------------------------------------

def _op(triplet: str) -> np.ndarray:
    """Parse 'x,y,z' style symmetry operation into 3x4 augmented matrix."""
    mat = np.zeros((3, 4))
    for i, part in enumerate(triplet.split(',')):
        part = part.strip()
        for token in _tokenize(part):
            if token == 'x':
                mat[i, 0] = 1
            elif token == '-x':
                mat[i, 0] = -1
            elif token == 'y':
                mat[i, 1] = 1
            elif token == '-y':
                mat[i, 1] = -1
            elif token == 'z':
                mat[i, 2] = 1
            elif token == '-z':
                mat[i, 2] = -1
            elif '/' in token:
                num, den = token.split('/')
                mat[i, 3] += float(num) / float(den)
            elif token.replace('.', '').replace('-', '').isdigit():
                mat[i, 3] += float(token)
    return mat


def _tokenize(expr: str) -> List[str]:
    """Tokenize a symmetry operation component like '-x+1/2'."""
    tokens = []
    current = ''
    for ch in expr:
        if ch in '+-' and current:
            tokens.append(current)
            current = ch
        else:
            current += ch
    if current:
        tokens.append(current)
    return tokens


# Common space groups with full symmetry operations
SPACE_GROUPS: Dict[str, Dict] = {
    "P1": {
        "number": 1,
        "system": "triclinic",
        "ops": [_op("x,y,z")],
    },
    "P-1": {
        "number": 2,
        "system": "triclinic",
        "ops": [_op("x,y,z"), _op("-x,-y,-z")],
    },
    "P2_1": {
        "number": 4,
        "system": "monoclinic",
        "ops": [_op("x,y,z"), _op("-x,y+1/2,-z")],
    },
    "C2": {
        "number": 5,
        "system": "monoclinic",
        "ops": [_op("x,y,z"), _op("-x,y,-z"),
                _op("x+1/2,y+1/2,z"), _op("-x+1/2,y+1/2,-z")],
    },
    "Cc": {
        "number": 9,
        "system": "monoclinic",
        "ops": [_op("x,y,z"), _op("x,-y,z+1/2"),
                _op("x+1/2,y+1/2,z"), _op("x+1/2,-y+1/2,z+1/2")],
    },
    "P2_1/c": {
        "number": 14,
        "system": "monoclinic",
        "ops": [_op("x,y,z"), _op("-x,y+1/2,-z+1/2"),
                _op("-x,-y,-z"), _op("x,-y+1/2,z+1/2")],
    },
    "C2/c": {
        "number": 15,
        "system": "monoclinic",
        "ops": [_op("x,y,z"), _op("-x,y,-z+1/2"),
                _op("-x,-y,-z"), _op("x,-y,z+1/2"),
                _op("x+1/2,y+1/2,z"), _op("-x+1/2,y+1/2,-z+1/2"),
                _op("-x+1/2,-y+1/2,-z"), _op("x+1/2,-y+1/2,z+1/2")],
    },
    "P2_12_12": {
        "number": 18,
        "system": "orthorhombic",
        "ops": [_op("x,y,z"), _op("-x,-y,z"),
                _op("-x+1/2,y+1/2,-z"), _op("x+1/2,-y+1/2,-z")],
    },
    "P2_12_12_1": {
        "number": 19,
        "system": "orthorhombic",
        "ops": [_op("x,y,z"), _op("-x+1/2,-y,z+1/2"),
                _op("-x,y+1/2,-z+1/2"), _op("x+1/2,-y+1/2,-z")],
    },
    "Pna2_1": {
        "number": 33,
        "system": "orthorhombic",
        "ops": [_op("x,y,z"), _op("-x,-y,z+1/2"),
                _op("-x+1/2,y+1/2,z+1/2"), _op("x+1/2,-y+1/2,z")],
    },
    "Pbca": {
        "number": 61,
        "system": "orthorhombic",
        "ops": [_op("x,y,z"), _op("-x+1/2,-y,z+1/2"),
                _op("-x,y+1/2,-z+1/2"), _op("x+1/2,-y+1/2,-z"),
                _op("-x,-y,-z"), _op("x+1/2,y,-z+1/2"),
                _op("x,-y+1/2,z+1/2"), _op("-x+1/2,y+1/2,z")],
    },
    "Pnma": {
        "number": 62,
        "system": "orthorhombic",
        "ops": [_op("x,y,z"), _op("-x+1/2,-y,z+1/2"),
                _op("-x,y+1/2,-z"), _op("x+1/2,-y+1/2,-z+1/2"),
                _op("-x,-y,-z"), _op("x+1/2,y,-z+1/2"),
                _op("x,-y+1/2,z"), _op("-x+1/2,y+1/2,z+1/2")],
    },
}

# Aliases for common alternative notations
SG_ALIASES: Dict[str, str] = {
    "P 1": "P1",
    "P -1": "P-1",
    "P 2_1": "P2_1",
    "P 21": "P2_1",
    "P21": "P2_1",
    "C 2": "C2",
    "C c": "Cc",
    "P 2_1/c": "P2_1/c",
    "P 21/c": "P2_1/c",
    "P21/c": "P2_1/c",
    "C 2/c": "C2/c",
    "P 2_1 2_1 2": "P2_12_12",
    "P 21 21 2": "P2_12_12",
    "P21212": "P2_12_12",
    "P 2_1 2_1 2_1": "P2_12_12_1",
    "P 21 21 21": "P2_12_12_1",
    "P212121": "P2_12_12_1",
    "P n a 2_1": "Pna2_1",
    "Pna21": "Pna2_1",
    "P b c a": "Pbca",
    "P n m a": "Pnma",
}


def resolve_sg(name: str) -> Optional[Dict]:
    """Resolve a space group name to its data dict, trying aliases."""
    name = name.strip()
    if name in SPACE_GROUPS:
        return SPACE_GROUPS[name]
    if name in SG_ALIASES:
        return SPACE_GROUPS[SG_ALIASES[name]]
    # Try removing spaces
    compact = name.replace(" ", "")
    if compact in SG_ALIASES:
        return SPACE_GROUPS[SG_ALIASES[compact]]
    for alias, canonical in SG_ALIASES.items():
        if alias.replace(" ", "") == compact:
            return SPACE_GROUPS[canonical]
    return None


def get_sg_name(name: str) -> str:
    """Get the canonical SG name."""
    name = name.strip()
    if name in SPACE_GROUPS:
        return name
    if name in SG_ALIASES:
        return SG_ALIASES[name]
    compact = name.replace(" ", "")
    if compact in SG_ALIASES:
        return SG_ALIASES[compact]
    for alias, canonical in SG_ALIASES.items():
        if alias.replace(" ", "") == compact:
            return canonical
    return name


def crystal_system(sg_name: str) -> str:
    """Return the crystal system for a given space group."""
    sg = resolve_sg(sg_name)
    if sg:
        return sg["system"]
    return "triclinic"


def sg_number(sg_name: str) -> int:
    """Return the ITA number for a space group."""
    sg = resolve_sg(sg_name)
    if sg:
        return sg["number"]
    return 1


def sg_ops_matrices(sg_name: str) -> List[np.ndarray]:
    """Return the symmetry operation matrices for a space group."""
    sg = resolve_sg(sg_name)
    if sg:
        return sg["ops"]
    return [_op("x,y,z")]


def symop_to_xyz(mat: np.ndarray) -> str:
    """Convert a 3x4 augmented matrix to 'x,y,z' string."""
    labels = ['x', 'y', 'z']
    parts = []
    for i in range(3):
        terms = []
        for j in range(3):
            if abs(mat[i, j]) > 0.01:
                if mat[i, j] > 0:
                    terms.append('+' + labels[j])
                else:
                    terms.append('-' + labels[j])
        # translation
        t = mat[i, 3]
        if abs(t) > 0.001:
            # express as fraction if close to common values
            frac = _to_fraction(t)
            if frac:
                terms.append(frac)
            else:
                terms.append(f"{t:+.4f}")
        s = ''.join(terms)
        if s.startswith('+'):
            s = s[1:]
        parts.append(s)
    return ','.join(parts)


def _to_fraction(val: float) -> Optional[str]:
    """Convert a float to a fraction string like +1/2, +1/4, etc."""
    for num in range(-3, 4):
        for den in [2, 3, 4, 6]:
            if abs(val - num / den) < 0.001 and num != 0:
                sign = '+' if num / den > 0 else ''
                n = abs(num)
                return f"{sign}{num}/{den}" if num < 0 else f"+{num}/{den}"
    return None


def calc_scattering_factor(element: str, s_sq: float) -> float:
    """
    Calculate the X-ray scattering factor f(s) for an element.
    s_sq = (sin(theta)/lambda)^2
    """
    if element not in SCATTERING_FACTORS:
        return 1.0  # fallback
    a1, b1, a2, b2, a3, b3, a4, b4, c = SCATTERING_FACTORS[element]
    f = (a1 * np.exp(-b1 * s_sq) +
         a2 * np.exp(-b2 * s_sq) +
         a3 * np.exp(-b3 * s_sq) +
         a4 * np.exp(-b4 * s_sq) + c)
    return f


def molecular_weight(formula_dict: Dict[str, int]) -> float:
    """Calculate molecular weight from {element: count} dict."""
    ATOMIC_WEIGHTS = {
        "H": 1.008, "He": 4.003, "Li": 6.941, "Be": 9.012, "B": 10.81,
        "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
        "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.086, "P": 30.974,
        "S": 32.065, "Cl": 35.453, "Ar": 39.948, "K": 39.098, "Ca": 40.078,
        "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845,
        "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.380, "Ga": 69.723,
        "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904, "Kr": 83.798,
        "Mo": 95.950, "Ag": 107.868, "Cd": 112.414, "Sn": 118.710, "I": 126.904,
        "Ba": 137.327, "W": 183.840, "Pt": 195.084, "Au": 196.967, "Pb": 207.200,
        "Bi": 208.980,
    }
    mw = 0.0
    for elem, count in formula_dict.items():
        mw += ATOMIC_WEIGHTS.get(elem, 0.0) * count
    return mw


# ---------------------------------------------------------------------------
# Charge-flipping algorithm
# ---------------------------------------------------------------------------

def charge_flipping(
    hkl_data: np.ndarray,
    cell: Tuple[float, float, float, float, float, float],
    sg_ops: List[np.ndarray],
    elements: List[str],
    grid_size: int = 72,
    n_trials: int = 2,
    max_iter: int = 200,
    delta_threshold: float = 0.8,
    convergence_window: int = 10,
    convergence_tol: float = 0.001,
) -> Tuple[np.ndarray, float]:
    """
    Charge-flipping structure solution.

    Args:
        hkl_data: Nx5 array of (h, k, l, F_obs, sigma)
        cell: (a, b, c, alpha, beta, gamma)
        sg_ops: list of 3x4 symmetry matrices
        elements: list of expected element symbols
        grid_size: FFT grid dimension
        n_trials: number of random-phase trials
        max_iter: max iterations per trial
        delta_threshold: flipping threshold as fraction of sigma(rho)
        convergence_window: iterations to check for convergence
        convergence_tol: relative change threshold for convergence

    Returns:
        (best_density_map, best_r_factor)
    """
    a, b, c, alpha, beta, gamma = cell
    h = hkl_data[:, 0].astype(int)
    k = hkl_data[:, 1].astype(int)
    l = hkl_data[:, 2].astype(int)
    f_obs = hkl_data[:, 3]

    # Normalize F_obs
    f_obs = f_obs / np.max(f_obs)

    best_rho = None
    best_r = float('inf')

    for trial in range(n_trials):
        # Random phases
        phases = np.random.uniform(0, 2 * np.pi, len(f_obs))
        f_calc = f_obs * np.exp(1j * phases)

        # Build structure factor array
        sf_grid = np.zeros((grid_size, grid_size, grid_size), dtype=complex)
        for idx in range(len(h)):
            hi, ki, li = int(h[idx]) % grid_size, int(k[idx]) % grid_size, int(l[idx]) % grid_size
            sf_grid[hi, ki, li] = f_calc[idx]
            # Friedel mate
            sf_grid[(-int(h[idx])) % grid_size,
                    (-int(k[idx])) % grid_size,
                    (-int(l[idx])) % grid_size] = np.conj(f_calc[idx])

        prev_rs = []
        for iteration in range(max_iter):
            # Inverse FFT to get density
            rho = np.real(np.fft.ifftn(sf_grid))

            # Charge flipping
            sigma_rho = np.std(rho)
            delta = delta_threshold * sigma_rho
            rho_flipped = np.where(rho < delta, -rho, rho)

            # Forward FFT
            sf_new = np.fft.fftn(rho_flipped)

            # Replace amplitudes with observed, keep calculated phases
            for idx in range(len(h)):
                hi = int(h[idx]) % grid_size
                ki = int(k[idx]) % grid_size
                li = int(l[idx]) % grid_size
                phase = np.angle(sf_new[hi, ki, li])
                sf_grid[hi, ki, li] = f_obs[idx] * np.exp(1j * phase)
                sf_grid[(-int(h[idx])) % grid_size,
                        (-int(k[idx])) % grid_size,
                        (-int(l[idx])) % grid_size] = f_obs[idx] * np.exp(-1j * phase)

            # R-factor
            f_calc_amps = np.array([
                abs(sf_grid[int(h[idx]) % grid_size,
                           int(k[idx]) % grid_size,
                           int(l[idx]) % grid_size])
                for idx in range(len(h))
            ])
            r_factor = np.sum(np.abs(f_obs - f_calc_amps)) / np.sum(f_obs)

            # Early convergence
            prev_rs.append(r_factor)
            if len(prev_rs) > convergence_window:
                recent = prev_rs[-convergence_window:]
                if max(recent) - min(recent) < convergence_tol:
                    break

        # Final density from best phases
        rho_final = np.real(np.fft.ifftn(sf_grid))

        if r_factor < best_r:
            best_r = r_factor
            best_rho = rho_final.copy()

    return best_rho, best_r


def find_atoms(
    rho: np.ndarray,
    cell: Tuple[float, float, float, float, float, float],
    elements: List[str],
    sigma_thresh: float = 3.5,
    min_distance: float = 0.8,
) -> List[Dict]:
    """
    Find atom positions from electron density map.

    Returns list of dicts with: element, frac_x, frac_y, frac_z, density
    """
    sigma = np.std(rho)
    mean = np.mean(rho)
    threshold = mean + sigma_thresh * sigma

    # Find peaks above threshold
    grid_size = rho.shape[0]
    peaks = []
    for ix in range(grid_size):
        for iy in range(grid_size):
            for iz in range(grid_size):
                val = rho[ix, iy, iz]
                if val < threshold:
                    continue
                # Check if local maximum (6-connected)
                is_max = True
                for dx, dy, dz in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]:
                    nx = (ix + dx) % grid_size
                    ny = (iy + dy) % grid_size
                    nz = (iz + dz) % grid_size
                    if rho[nx, ny, nz] > val:
                        is_max = False
                        break
                if is_max:
                    fx = ix / grid_size
                    fy = iy / grid_size
                    fz = iz / grid_size
                    peaks.append((fx, fy, fz, val))

    # Sort by density (descending)
    peaks.sort(key=lambda p: -p[3])

    # Filter by minimum distance
    a, b, c = cell[0], cell[1], cell[2]
    filtered = []
    for fx, fy, fz, val in peaks:
        too_close = False
        for existing in filtered:
            dx = (fx - existing[0]) % 1.0
            if dx > 0.5:
                dx -= 1.0
            dy = (fy - existing[1]) % 1.0
            if dy > 0.5:
                dy -= 1.0
            dz = (fz - existing[2]) % 1.0
            if dz > 0.5:
                dz -= 1.0
            dist = np.sqrt((dx * a) ** 2 + (dy * b) ** 2 + (dz * c) ** 2)
            if dist < min_distance:
                too_close = True
                break
        if not too_close:
            filtered.append((fx, fy, fz, val))

    # Assign elements by density (heaviest → highest density)
    # Sort elements by atomic number (proxy: scattering factor at s=0)
    elem_weights = []
    for elem in elements:
        if elem in SCATTERING_FACTORS:
            f0 = calc_scattering_factor(elem, 0.0)
        else:
            f0 = 1.0
        elem_weights.append((elem, f0))
    elem_weights.sort(key=lambda x: -x[1])

    atoms = []
    for i, (fx, fy, fz, val) in enumerate(filtered):
        # Assign element: distribute proportionally
        # Simple heuristic: heaviest elements get highest-density peaks
        if len(elem_weights) == 1:
            elem = elem_weights[0][0]
        else:
            # Split peaks into groups by element count
            elem_idx = min(i * len(elem_weights) // max(len(filtered), 1),
                          len(elem_weights) - 1)
            elem = elem_weights[elem_idx][0]

        atoms.append({
            "element": elem,
            "frac_x": fx % 1.0,
            "frac_y": fy % 1.0,
            "frac_z": fz % 1.0,
            "density": val,
        })

    return atoms
