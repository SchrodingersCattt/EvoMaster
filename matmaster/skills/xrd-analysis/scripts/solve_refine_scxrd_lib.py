"""
Library helpers for solve_refine_scxrd.py — charge-flipping, atom finding, scattering factors.
"""
import numpy as np
from typing import Dict, List, Tuple, Optional

# ---------------------------------------------------------------------------
# Cromer-Mann scattering factor coefficients  a1 b1 a2 b2 a3 b3 a4 b4 c
# Ref: International Tables for Crystallography, Vol C, Table 6.1.1.4
# ---------------------------------------------------------------------------
CROMER_MANN: Dict[str, Tuple[float, ...]] = {
    "H":  (0.489918, 20.6593, 0.262003, 7.74039, 0.196767, 49.5519, 0.049879, 2.20159, 0.001305),
    "C":  (2.31000, 20.8439, 1.02000, 10.2075, 1.58860, 0.56870, 0.86500, 51.6512, 0.21560),
    "N":  (12.2126, 0.00570, 3.13220, 9.89330, 2.01250, 28.9975, 1.16630, 0.58260, -11.529),
    "O":  (3.04850, 13.2771, 2.28680, 5.70110, 1.54630, 0.32390, 0.86700, 32.9089, 0.25080),
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
    "Br": (17.1789, 2.17230, 5.23580, 16.5796, 5.63770, 0.26090, 3.98510, 41.4328, 2.95570),
    "Mo": (3.70250, 0.27720, 17.2356, 1.09580, 12.8876, 11.0040, 3.74290, 61.6584, 4.38750),
    "Ag": (19.2808, 0.64460, 16.6885, 7.47260, 4.80450, 24.6605, 1.04630, 99.8156, 5.17900),
    "Cd": (19.2214, 0.59460, 17.6444, 6.90890, 4.46100, 24.7008, 1.60290, 87.4825, 5.06940),
    "Sn": (19.1889, 5.83030, 19.1005, 0.50310, 4.45850, 26.8909, 2.46630, 83.9571, 4.78210),
    "Ba": (20.3361, 3.21600, 19.2970, 0.27560, 10.8880, 20.2073, 2.69590, 167.202, 2.77310),
    "W":  (29.0818, 1.72029, 15.4300, 9.22590, 14.4327, 0.32170, 5.11982, 57.0560, 9.88750),
    "Pt": (27.0059, 1.51293, 17.7639, 8.81174, 15.7131, 0.42459, 5.78370, 38.6103, 11.6883),
    "Au": (16.8819, 0.46110, 18.5913, 8.62160, 25.5582, 1.48260, 5.86000, 36.3956, 12.0658),
    "Pb": (31.0617, 0.69020, 13.0637, 2.35760, 18.4420, 8.61800, 5.96960, 47.2579, 13.4118),
    "Bi": (33.3689, 0.70400, 12.9510, 2.92380, 16.5877, 8.79370, 6.46920, 48.0093, 13.5782),
}


def scattering_factor(element: str, sin_theta_over_lambda: np.ndarray) -> np.ndarray:
    """Compute f0 from Cromer-Mann coefficients."""
    coeff = CROMER_MANN.get(element)
    if coeff is None:
        raise ValueError(f"No scattering factors for element '{element}'")
    a1, b1, a2, b2, a3, b3, a4, b4, c = coeff
    s2 = sin_theta_over_lambda ** 2
    return (a1 * np.exp(-b1 * s2) + a2 * np.exp(-b2 * s2) +
            a3 * np.exp(-b3 * s2) + a4 * np.exp(-b4 * s2) + c)


# ---------------------------------------------------------------------------
# Space group operations  (rotation matrix 3×3, translation 3)
# ---------------------------------------------------------------------------
_SG_OPS: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {}

def _op(rot: List[List[int]], trans: List[float]) -> Tuple[np.ndarray, np.ndarray]:
    return (np.array(rot, dtype=float), np.array(trans, dtype=float))

# Identity always present
_I = _op([[1,0,0],[0,1,0],[0,0,1]], [0,0,0])

# P1 (#1)
_SG_OPS["P1"] = [_I]
_SG_OPS["1"] = _SG_OPS["P1"]

# P-1 (#2)
_SG_OPS["P-1"] = [_I, _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0])]
_SG_OPS["2"] = _SG_OPS["P-1"]

# P21 (#4)
_SG_OPS["P21"] = [_I, _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0])]
_SG_OPS["P2_1"] = _SG_OPS["P21"]
_SG_OPS["4"] = _SG_OPS["P21"]

# C2 (#5)
_SG_OPS["C2"] = [_I, _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0,0])]
_SG_OPS["5"] = _SG_OPS["C2"]

# Cc (#9)
_SG_OPS["Cc"] = [_I, _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0,0.5])]
_SG_OPS["9"] = _SG_OPS["Cc"]

# P21/c (#14) — including P21/n and P21/a settings
_SG_OPS["P21/c"] = [
    _I,
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0]),
    _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0.5,0]),  # actually [0,0.5,0.5]
]
# Fix: P21/c has: x,y,z; -x,y+1/2,-z+1/2; -x,-y,-z; x,-y+1/2,z+1/2
_SG_OPS["P21/c"] = [
    _I,
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0.5]),
    _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0.5,0.5]),
]
_SG_OPS["P2_1/c"] = _SG_OPS["P21/c"]
_SG_OPS["P21/n"] = _SG_OPS["P21/c"]  # alias (different cell setting, same #14)
_SG_OPS["P2_1/n"] = _SG_OPS["P21/c"]
_SG_OPS["P21/a"] = _SG_OPS["P21/c"]
_SG_OPS["P2_1/a"] = _SG_OPS["P21/c"]
_SG_OPS["14"] = _SG_OPS["P21/c"]

# C2/c (#15)
_SG_OPS["C2/c"] = [
    _I,
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0,0.5]),
    _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0,0.5]),
]
_SG_OPS["15"] = _SG_OPS["C2/c"]

# P21212 (#18)
_SG_OPS["P21212"] = [
    _I,
    _op([[-1,0,0],[0,-1,0],[0,0,1]], [0,0,0]),
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0.5,0.5,0]),
    _op([[1,0,0],[0,-1,0],[0,0,-1]], [0.5,0.5,0]),
]
_SG_OPS["18"] = _SG_OPS["P21212"]

# P212121 (#19)
_SG_OPS["P212121"] = [
    _I,
    _op([[-1,0,0],[0,-1,0],[0,0,1]], [0.5,0,0.5]),
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0.5]),
    _op([[1,0,0],[0,-1,0],[0,0,-1]], [0.5,0.5,0]),
]
_SG_OPS["P2_12_12_1"] = _SG_OPS["P212121"]
_SG_OPS["19"] = _SG_OPS["P212121"]

# Pna21 (#33)
_SG_OPS["Pna21"] = [
    _I,
    _op([[-1,0,0],[0,-1,0],[0,0,1]], [0,0,0.5]),
    _op([[-1,0,0],[0,1,0],[0,0,1]], [0.5,0.5,0.5]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0.5,0.5,0]),  # x+1/2,-y+1/2,z
]
_SG_OPS["33"] = _SG_OPS["Pna21"]

# Pbca (#61)
_SG_OPS["Pbca"] = [
    _I,
    _op([[-1,0,0],[0,-1,0],[0,0,1]], [0.5,0,0.5]),
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0.5]),
    _op([[1,0,0],[0,-1,0],[0,0,-1]], [0.5,0.5,0]),
    _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0]),
    _op([[1,0,0],[0,1,0],[0,0,-1]], [0.5,0,0.5]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0.5,0.5]),
    _op([[-1,0,0],[0,1,0],[0,0,1]], [0.5,0.5,0]),
]
_SG_OPS["61"] = _SG_OPS["Pbca"]

# Pnma (#62)
_SG_OPS["Pnma"] = [
    _I,
    _op([[-1,0,0],[0,-1,0],[0,0,1]], [0.5,0,0.5]),
    _op([[-1,0,0],[0,1,0],[0,0,-1]], [0,0.5,0]),
    _op([[1,0,0],[0,-1,0],[0,0,-1]], [0.5,0.5,0.5]),
    _op([[-1,0,0],[0,-1,0],[0,0,-1]], [0,0,0]),
    _op([[1,0,0],[0,1,0],[0,0,-1]], [0.5,0,0.5]),
    _op([[1,0,0],[0,-1,0],[0,0,1]], [0,0.5,0]),
    _op([[-1,0,0],[0,1,0],[0,0,1]], [0.5,0.5,0.5]),
]
_SG_OPS["62"] = _SG_OPS["Pnma"]

# SG number → name mapping
SG_NUMBER_TO_NAME = {
    1: "P1", 2: "P-1", 4: "P21", 5: "C2", 9: "Cc",
    14: "P21/c", 15: "C2/c", 18: "P21212", 19: "P212121",
    33: "Pna21", 61: "Pbca", 62: "Pnma",
}

# Crystal system from SG number
def crystal_system(sg_num: int) -> str:
    if sg_num <= 2: return "triclinic"
    if sg_num <= 15: return "monoclinic"
    if sg_num <= 74: return "orthorhombic"
    if sg_num <= 142: return "tetragonal"
    if sg_num <= 167: return "trigonal"
    if sg_num <= 194: return "hexagonal"
    return "cubic"


def get_sg_ops(sg_name: str) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Return list of (rotation_matrix, translation_vector) for the space group."""
    # Try direct lookup
    ops = _SG_OPS.get(sg_name)
    if ops is not None:
        return ops
    # Try stripping spaces and underscores
    clean = sg_name.replace(" ", "").replace("_", "")
    ops = _SG_OPS.get(clean)
    if ops is not None:
        return ops
    # Try SG number
    try:
        num = int(sg_name)
        name = SG_NUMBER_TO_NAME.get(num)
        if name:
            return _SG_OPS[name]
    except ValueError:
        pass
    # Fallback to P1
    print(f"WARNING: Unknown space group '{sg_name}', falling back to P1")
    return _SG_OPS["P1"]


def get_sg_number(sg_name: str) -> int:
    """Best-effort mapping from SG name to ITA number."""
    name_to_num = {v: k for k, v in SG_NUMBER_TO_NAME.items()}
    clean = sg_name.replace(" ", "").replace("_", "")
    if clean in name_to_num:
        return name_to_num[clean]
    for name, num in name_to_num.items():
        if name.replace(" ", "").replace("_", "") == clean:
            return num
    # Check aliases
    alias_map = {
        "P21/n": 14, "P2_1/n": 14, "P21/a": 14, "P2_1/a": 14,
        "P2_1": 4, "P2_12_12_1": 19,
    }
    if clean in alias_map:
        return alias_map[clean]
    try:
        return int(sg_name)
    except ValueError:
        return 1


def symop_to_xyz(rot: np.ndarray, trans: np.ndarray) -> str:
    """Convert rotation matrix + translation to 'x,y,z' string notation."""
    axes = ['x', 'y', 'z']
    parts = []
    for i in range(3):
        terms = []
        for j in range(3):
            v = int(round(rot[i, j]))
            if v == 0:
                continue
            sign = '+' if v > 0 else '-'
            if terms or sign == '-':
                terms.append(sign)
            label = axes[j]
            terms.append(label)
        t = trans[i] % 1.0
        if t > 0.001:
            # Express as fraction
            from fractions import Fraction
            frac = Fraction(t).limit_denominator(12)
            sign = '+'
            if terms:
                terms.append(sign)
            terms.append(f"{frac.numerator}/{frac.denominator}")
        parts.append(''.join(terms) if terms else '0')
    return ','.join(parts)


# ---------------------------------------------------------------------------
# Charge-flipping algorithm
# ---------------------------------------------------------------------------
def charge_flipping(
    hkl_data: np.ndarray,  # Nx5: h, k, l, F2, sigF2
    cell: Tuple[float, ...],  # a, b, c, alpha, beta, gamma
    sg_ops: List[Tuple[np.ndarray, np.ndarray]],
    grid: int = 72,
    cycles: int = 400,
    sigma_thresh: float = 3.5,
    seed: int = 42,
) -> Optional[np.ndarray]:
    """
    Run charge-flipping to get electron density map.
    Returns 3D density array or None on failure.
    """
    rng = np.random.RandomState(seed)

    h = hkl_data[:, 0].astype(int)
    k = hkl_data[:, 1].astype(int)
    l = hkl_data[:, 2].astype(int)
    F2 = hkl_data[:, 3]
    F_obs = np.sqrt(np.maximum(F2, 0.0))

    # Initialize with random phases
    phases = rng.uniform(0, 2 * np.pi, len(F_obs))

    density = np.zeros((grid, grid, grid), dtype=complex)

    prev_phases = None
    for cycle in range(cycles):
        # Build F(hkl) with observed amplitudes and current phases
        F_hkl = F_obs * np.exp(1j * phases)

        # Place into 3D grid
        density[:] = 0
        for i in range(len(h)):
            hi, ki, li = int(h[i]) % grid, int(k[i]) % grid, int(l[i]) % grid
            density[hi, ki, li] = F_hkl[i]
            # Friedel mate
            density[(-int(h[i])) % grid, (-int(k[i])) % grid, (-int(l[i])) % grid] = np.conj(F_hkl[i])

        # FFT to real space
        rho = np.real(np.fft.ifftn(density))

        # Charge flipping: flip sign where density is below threshold
        threshold = sigma_thresh * np.std(rho) * 0.1  # Use fraction of sigma
        rho[rho < threshold] = -rho[rho < threshold]

        # FFT back to reciprocal space
        F_new = np.fft.fftn(rho)

        # Extract new phases at observed reflections, keep observed amplitudes
        new_phases = np.zeros(len(h))
        for i in range(len(h)):
            hi, ki, li = int(h[i]) % grid, int(k[i]) % grid, int(l[i]) % grid
            new_phases[i] = np.angle(F_new[hi, ki, li])

        # Check convergence
        if prev_phases is not None and cycle >= 30:
            phase_diff = np.mean(np.abs(np.exp(1j * new_phases) - np.exp(1j * prev_phases)))
            if phase_diff < 0.02:
                print(f"  Charge-flipping converged at cycle {cycle}")
                break

        prev_phases = new_phases.copy()
        phases = new_phases

    # Final density
    density[:] = 0
    F_hkl = F_obs * np.exp(1j * phases)
    for i in range(len(h)):
        hi, ki, li = int(h[i]) % grid, int(k[i]) % grid, int(l[i]) % grid
        density[hi, ki, li] = F_hkl[i]
        density[(-int(h[i])) % grid, (-int(k[i])) % grid, (-int(l[i])) % grid] = np.conj(F_hkl[i])

    rho_final = np.real(np.fft.ifftn(density))
    return rho_final


def find_atoms_in_density(
    rho: np.ndarray,
    cell: Tuple[float, ...],
    sigma_thresh: float = 3.5,
    min_distance: float = 0.8,
) -> List[Tuple[float, float, float, float]]:
    """
    Find atom positions from electron density map.
    Returns list of (x_frac, y_frac, z_frac, peak_height).
    """
    grid = rho.shape[0]
    mean_rho = np.mean(rho)
    std_rho = np.std(rho)
    threshold = mean_rho + sigma_thresh * std_rho

    # Find peaks above threshold
    peaks = []
    for i in range(grid):
        for j in range(grid):
            for kk in range(grid):
                if rho[i, j, kk] > threshold:
                    # Check if local maximum (6 neighbors)
                    is_max = True
                    for di, dj, dk in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]:
                        ni = (i + di) % grid
                        nj = (j + dj) % grid
                        nk = (kk + dk) % grid
                        if rho[ni, nj, nk] > rho[i, j, kk]:
                            is_max = False
                            break
                    if is_max:
                        x = i / grid
                        y = j / grid
                        z = kk / grid
                        peaks.append((x, y, z, rho[i, j, kk]))

    # Sort by peak height (descending)
    peaks.sort(key=lambda p: -p[3])

    # Remove duplicates that are too close
    a, b, c = cell[0], cell[1], cell[2]
    filtered = []
    for peak in peaks:
        too_close = False
        for existing in filtered:
            dx = (peak[0] - existing[0]) % 1.0
            if dx > 0.5: dx -= 1.0
            dy = (peak[1] - existing[1]) % 1.0
            if dy > 0.5: dy -= 1.0
            dz = (peak[2] - existing[2]) % 1.0
            if dz > 0.5: dz -= 1.0
            dist = np.sqrt((dx * a) ** 2 + (dy * b) ** 2 + (dz * c) ** 2)
            if dist < min_distance:
                too_close = True
                break
        if not too_close:
            filtered.append(peak)

    return filtered


def molecular_weight(elements: List[str], counts: List[int]) -> float:
    """Calculate molecular weight from element list and counts."""
    WEIGHTS = {
        "H": 1.008, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998,
        "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.086, "P": 30.974,
        "S": 32.065, "Cl": 35.453, "K": 39.098, "Ca": 40.078, "Ti": 47.867,
        "Mn": 54.938, "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546,
        "Zn": 65.38, "Ga": 69.723, "Ge": 72.630, "As": 74.922, "Se": 78.971,
        "Br": 79.904, "Mo": 95.95, "Ag": 107.87, "Cd": 112.41, "Sn": 118.71,
        "Ba": 137.33, "W": 183.84, "Pt": 195.08, "Au": 196.97, "Pb": 207.2,
        "Bi": 208.98,
    }
    mw = 0.0
    for elem, cnt in zip(elements, counts):
        mw += WEIGHTS.get(elem, 0.0) * cnt
    return mw
