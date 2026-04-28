#!/usr/bin/env python3
"""
refine_lattice_pxrd.py — Pawley-type lattice parameter refinement from PXRD data.

Usage:
  python3 refine_lattice_pxrd.py --data pattern.xy --sg "P2_1/c" \\
      --cell "a=10.5,b=9.6,c=10.2,beta=98.5" --wavelength 1.5406 \\
      -o result.json

  # Multi-temperature mode:
  python3 refine_lattice_pxrd.py --data ./ --sg "Pm-3m" \\
      --cell "a=3.905" --wavelength 1.5406 --multi-temp \\
      -o result.json

Requires: numpy, scipy
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks


# ---------------------------------------------------------------------------
# Crystal system constraints
# ---------------------------------------------------------------------------

CRYSTAL_SYSTEMS = {
    "cubic": ["a"],
    "tetragonal": ["a", "c"],
    "orthorhombic": ["a", "b", "c"],
    "hexagonal": ["a", "c"],
    "trigonal": ["a", "c"],
    "monoclinic": ["a", "b", "c", "beta"],
    "triclinic": ["a", "b", "c", "alpha", "beta", "gamma"],
}

# Space group -> crystal system (subset)
SG_CRYSTAL_SYSTEM = {
    "Pm-3m": "cubic",
    "Fm-3m": "cubic",
    "Im-3m": "cubic",
    "Fd-3m": "cubic",
    "Pa-3": "cubic",
    "P4/mmm": "tetragonal",
    "I4/mmm": "tetragonal",
    "P6_3/mmc": "hexagonal",
    "P6_3mc": "hexagonal",
    "R-3m": "trigonal",
    "R3m": "trigonal",
    "Pnma": "orthorhombic",
    "Pbca": "orthorhombic",
    "P2_12_12_1": "orthorhombic",
    "P212121": "orthorhombic",
    "Cmcm": "orthorhombic",
    "Cmc21": "orthorhombic",
    "P2_1/c": "monoclinic",
    "P21/c": "monoclinic",
    "C2/c": "monoclinic",
    "C2/m": "monoclinic",
    "P-1": "triclinic",
    "P1": "triclinic",
}


def guess_crystal_system(sg: str) -> str:
    """Guess crystal system from space group name."""
    sg_clean = sg.strip().replace(" ", "")
    if sg_clean in SG_CRYSTAL_SYSTEM:
        return SG_CRYSTAL_SYSTEM[sg_clean]
    # Heuristic from first letter / numbers
    if sg_clean.startswith(("Pm-3", "Fm-3", "Im-3", "Fd-3", "Pa-3", "Ia-3")):
        return "cubic"
    if "4" in sg_clean:
        return "tetragonal"
    if "6" in sg_clean or "3" in sg_clean:
        if sg_clean.startswith("R"):
            return "trigonal"
        return "hexagonal"
    if sg_clean.startswith(("P2_1/", "P21/", "C2/", "C2", "Cc", "Pc")):
        return "monoclinic"
    return "orthorhombic"


def cell_to_params(cell: dict, crystal_system: str) -> np.ndarray:
    """Extract free parameters for the crystal system."""
    free = CRYSTAL_SYSTEMS[crystal_system]
    return np.array([cell[k] for k in free])


def params_to_cell(params: np.ndarray, crystal_system: str, base_cell: dict) -> dict:
    """Reconstruct full cell from free parameters."""
    cell = dict(base_cell)
    free = CRYSTAL_SYSTEMS[crystal_system]
    for i, k in enumerate(free):
        cell[k] = params[i]

    # Apply constraints
    if crystal_system == "cubic":
        cell["b"] = cell["c"] = cell["a"]
        cell["alpha"] = cell["beta"] = cell["gamma"] = 90.0
    elif crystal_system == "tetragonal":
        cell["b"] = cell["a"]
        cell["alpha"] = cell["beta"] = cell["gamma"] = 90.0
    elif crystal_system == "hexagonal":
        cell["b"] = cell["a"]
        cell["alpha"] = cell["beta"] = 90.0
        cell["gamma"] = 120.0
    elif crystal_system == "trigonal":
        cell["b"] = cell["a"]
        cell["alpha"] = cell["beta"] = 90.0
        cell["gamma"] = 120.0
    elif crystal_system == "orthorhombic":
        cell["alpha"] = cell["beta"] = cell["gamma"] = 90.0
    elif crystal_system == "monoclinic":
        cell["alpha"] = cell["gamma"] = 90.0

    return cell


# ---------------------------------------------------------------------------
# Peak positions from cell
# ---------------------------------------------------------------------------


def calc_two_theta(
    h: int, k: int, l_idx: int, cell: dict, wavelength: float
) -> float | None:
    """Calculate 2θ for a reflection (h,k,l) given cell and wavelength."""
    a, b, c = cell["a"], cell["b"], cell["c"]
    al = np.radians(cell.get("alpha", 90.0))
    be = np.radians(cell.get("beta", 90.0))
    ga = np.radians(cell.get("gamma", 90.0))

    cos_al, cos_be, cos_ga = np.cos(al), np.cos(be), np.cos(ga)
    sin_al, sin_be, sin_ga = np.sin(al), np.sin(be), np.sin(ga)

    vol = (
        a
        * b
        * c
        * np.sqrt(1 - cos_al**2 - cos_be**2 - cos_ga**2 + 2 * cos_al * cos_be * cos_ga)
    )
    if vol < 1e-6:
        return None

    # Reciprocal metric tensor
    ar = b * c * sin_al / vol
    br = a * c * sin_be / vol
    cr = a * b * sin_ga / vol

    cos_al_r = (cos_be * cos_ga - cos_al) / (sin_be * sin_ga + 1e-12)
    cos_be_r = (cos_al * cos_ga - cos_be) / (sin_al * sin_ga + 1e-12)
    cos_ga_r = (cos_al * cos_be - cos_ga) / (sin_al * sin_be + 1e-12)

    d_star_sq = (
        (h * ar) ** 2
        + (k * br) ** 2
        + (l_idx * cr) ** 2
        + 2 * h * k * ar * br * cos_ga_r
        + 2 * h * l_idx * ar * cr * cos_be_r
        + 2 * k * l_idx * br * cr * cos_al_r
    )

    sin_theta = wavelength * np.sqrt(d_star_sq) / 2.0
    if abs(sin_theta) > 1.0:
        return None
    return 2 * np.degrees(np.arcsin(sin_theta))


def generate_hkl_list(
    cell: dict, wavelength: float, two_theta_max: float = 80.0, h_max: int = 10
) -> list[tuple[int, int, int, float]]:
    """Generate (h, k, l, 2θ) for all reflections within range."""
    reflections = []
    for h in range(-h_max, h_max + 1):
        for k in range(-h_max, h_max + 1):
            for l_idx in range(-h_max, h_max + 1):
                if h == 0 and k == 0 and l_idx == 0:
                    continue
                tt = calc_two_theta(h, k, l_idx, cell, wavelength)
                if tt is not None and 5 < tt < two_theta_max:
                    reflections.append((h, k, l_idx, tt))
    # Remove duplicates (same 2theta within tolerance)
    reflections.sort(key=lambda x: x[3])
    return reflections


# ---------------------------------------------------------------------------
# Peak finding from PXRD pattern
# ---------------------------------------------------------------------------


def load_xy_pattern(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Load a 2-column XY pattern (2θ, intensity)."""
    data = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("'"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    data.append((x, y))
                except ValueError:
                    continue
    if not data:
        raise ValueError(f"No data points in {path}")
    data = np.array(data)
    return data[:, 0], data[:, 1]


def find_peaks_adaptive(
    two_theta: np.ndarray, intensity: np.ndarray, min_peaks: int = 8
) -> np.ndarray:
    """
    Find peaks with adaptive threshold.
    Falls back to progressively lower thresholds if too few peaks found.
    """
    # Baseline subtraction (simple: percentile)
    baseline = np.percentile(intensity, 10)
    y = intensity - baseline
    y[y < 0] = 0
    y_max = np.max(y)

    for frac in [1.0, 0.5, 0.25, 0.1, 0.05]:
        height = frac * y_max * 0.05
        distance = max(3, int(len(two_theta) / 200))
        prominence = frac * y_max * 0.02

        indices, props = find_peaks(
            y, height=height, distance=distance, prominence=prominence
        )
        if len(indices) >= min_peaks:
            return two_theta[indices]

    # Last resort: top N peaks
    indices, _ = find_peaks(y, distance=3)
    if len(indices) > 0:
        prominences = y[indices]
        top = np.argsort(prominences)[-min_peaks:]
        return two_theta[indices[top]]

    return np.array([])


# ---------------------------------------------------------------------------
# Pawley refinement (peak-position matching)
# ---------------------------------------------------------------------------


def pawley_refine(
    obs_peaks: np.ndarray,
    cell: dict,
    crystal_system: str,
    wavelength: float,
    two_theta_max: float = None,
) -> tuple[dict, float]:
    """
    Pawley-type refinement: refine cell to match observed peak positions.

    Returns (refined_cell, wR).
    """
    if two_theta_max is None:
        two_theta_max = np.max(obs_peaks) + 5.0

    p0 = cell_to_params(cell, crystal_system)

    def residuals(p):
        trial_cell = params_to_cell(p, crystal_system, cell)
        # Prevent negative / zero params
        for k in ["a", "b", "c"]:
            if trial_cell[k] < 0.5:
                return np.ones(len(obs_peaks)) * 1000.0

        hkl_list = generate_hkl_list(
            trial_cell, wavelength, two_theta_max=two_theta_max
        )
        if not hkl_list:
            return np.ones(len(obs_peaks)) * 1000.0

        calc_peaks = np.array([x[3] for x in hkl_list])
        calc_peaks = np.unique(np.round(calc_peaks, 3))

        # Match each observed peak to nearest calculated
        resid = []
        for obs_tt in obs_peaks:
            diffs = np.abs(calc_peaks - obs_tt)
            min_diff = np.min(diffs)
            resid.append(min_diff)
        return np.array(resid)

    # Bounds
    lower = p0 * 0.9
    upper = p0 * 1.1
    # Angle bounds
    free = CRYSTAL_SYSTEMS[crystal_system]
    for i, k in enumerate(free):
        if k in ("alpha", "beta", "gamma"):
            lower[i] = max(p0[i] - 10.0, 30.0)
            upper[i] = min(p0[i] + 10.0, 150.0)

    try:
        result = least_squares(
            residuals, p0, bounds=(lower, upper), method="trf", max_nfev=500
        )
        refined_cell = params_to_cell(result.x, crystal_system, cell)

        # Calculate wR
        r = residuals(result.x)
        wr = np.sqrt(np.sum(r**2) / (len(obs_peaks) * np.mean(obs_peaks) ** 2 + 1e-12))

        return refined_cell, float(wr)

    except Exception as e:
        print(f"WARNING: Refinement failed: {e}", file=sys.stderr)
        return cell, 1.0


# ---------------------------------------------------------------------------
# Multi-temperature mode
# ---------------------------------------------------------------------------


def parse_temperature(filename: str) -> float | None:
    """Extract temperature from filename (e.g. 'pattern_300K.xy' -> 300.0)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[Kk]", filename)
    if m:
        return float(m.group(1))
    # Try plain numbers in filename
    m = re.search(r"_(\d{2,4})(?:_|\.|$)", filename)
    if m:
        val = float(m.group(1))
        if 100 <= val <= 2000:
            return val
    return None


def thermal_expansion_fit(temps: np.ndarray, params: np.ndarray) -> dict:
    """Linear fit of lattice parameter vs temperature."""
    if len(temps) < 2:
        return {"slope": 0.0, "intercept": float(params[0]), "R2": 0.0}

    coeffs = np.polyfit(temps, params, 1)
    slope, intercept = coeffs
    pred = np.polyval(coeffs, temps)
    ss_res = np.sum((params - pred) ** 2)
    ss_tot = np.sum((params - np.mean(params)) ** 2)
    r2 = 1 - ss_res / (ss_tot + 1e-12)

    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "R2": float(r2),
    }


def detect_phase_transitions(
    temps: np.ndarray, volumes: np.ndarray, threshold: float = 0.03
) -> list[int]:
    """
    Detect phase transition indices where volume jump exceeds threshold (fractional).
    """
    if len(temps) < 3:
        return []
    transitions = []
    sorted_idx = np.argsort(temps)
    sorted_vols = volumes[sorted_idx]
    for i in range(1, len(sorted_vols)):
        frac_change = abs(sorted_vols[i] - sorted_vols[i - 1]) / (
            sorted_vols[i - 1] + 1e-12
        )
        if frac_change > threshold:
            transitions.append(int(sorted_idx[i]))
    return transitions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="PXRD Pawley lattice parameter refinement"
    )
    parser.add_argument(
        "--data", required=True, help="Path to XY file or directory of XY files"
    )
    parser.add_argument("--sg", required=True, help="Space group symbol")
    parser.add_argument(
        "--cell",
        required=True,
        help="Initial cell as 'a=X,b=Y,c=Z[,alpha=A,beta=B,gamma=G]'",
    )
    parser.add_argument(
        "--wavelength",
        type=float,
        default=1.5406,
        help="X-ray wavelength in Å (default: 1.5406 CuKα)",
    )
    parser.add_argument(
        "--multi-temp",
        action="store_true",
        help="Multi-temperature mode (data is directory)",
    )
    parser.add_argument(
        "--output", "-o", default="result.json", help="Output JSON path"
    )
    parser.add_argument("--debug-plot", help="Directory for debug plots")
    args = parser.parse_args()

    # Parse initial cell
    cell = {"a": 0, "b": 0, "c": 0, "alpha": 90.0, "beta": 90.0, "gamma": 90.0}
    for part in args.cell.split(","):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            cell[key.strip()] = float(val.strip())
        else:
            # Positional
            pass

    crystal_system = guess_crystal_system(args.sg)
    # Apply constraints to initial cell
    if crystal_system == "cubic":
        cell["b"] = cell["c"] = cell["a"]
    elif crystal_system in ("tetragonal", "hexagonal", "trigonal"):
        cell["b"] = cell["a"]

    print("=== PXRD Pawley Refinement ===")
    print(f"Space group: {args.sg} ({crystal_system})")
    print(f"Initial cell: {cell}")
    print(f"Wavelength: {args.wavelength:.5f} Å")

    # Determine files to process
    data_path = Path(args.data)
    if data_path.is_dir():
        files = sorted(
            glob.glob(str(data_path / "*.xy"))
            + glob.glob(str(data_path / "*.dat"))
            + glob.glob(str(data_path / "*.csv"))
            + glob.glob(str(data_path / "*.txt"))
        )
        if not files:
            # Try all files
            files = sorted(
                str(p)
                for p in data_path.iterdir()
                if p.is_file() and not p.name.startswith(".")
            )
    else:
        files = [str(data_path)]

    print(f"Processing {len(files)} pattern(s)")

    results = []
    for fpath in files:
        fname = os.path.basename(fpath)
        print(f"\n--- {fname} ---")
        try:
            two_theta, intensity = load_xy_pattern(fpath)
            print(
                f"  Loaded {len(two_theta)} points, 2θ range: [{two_theta[0]:.2f}, {two_theta[-1]:.2f}]"
            )

            obs_peaks = find_peaks_adaptive(two_theta, intensity)
            print(f"  Found {len(obs_peaks)} peaks")

            if len(obs_peaks) < 3:
                print("  WARNING: Too few peaks, skipping")
                results.append(
                    {
                        "file": fname,
                        "success": False,
                        "error": "too few peaks",
                    }
                )
                continue

            refined_cell, wr = pawley_refine(
                obs_peaks,
                cell,
                crystal_system,
                args.wavelength,
                two_theta_max=two_theta[-1],
            )

            vol = (
                refined_cell["a"]
                * refined_cell["b"]
                * refined_cell["c"]
                * np.sqrt(
                    1
                    - np.cos(np.radians(refined_cell.get("alpha", 90))) ** 2
                    - np.cos(np.radians(refined_cell.get("beta", 90))) ** 2
                    - np.cos(np.radians(refined_cell.get("gamma", 90))) ** 2
                    + 2
                    * np.cos(np.radians(refined_cell.get("alpha", 90)))
                    * np.cos(np.radians(refined_cell.get("beta", 90)))
                    * np.cos(np.radians(refined_cell.get("gamma", 90)))
                )
            )

            temp = parse_temperature(fname)

            entry = {
                "file": fname,
                "success": True,
                "wR": round(wr, 6),
                "cell": {k: round(v, 5) for k, v in refined_cell.items()},
                "volume": round(vol, 3),
                "n_peaks": len(obs_peaks),
            }
            if temp is not None:
                entry["temperature_K"] = temp
            results.append(entry)

            print(
                f"  Refined: a={refined_cell['a']:.4f} b={refined_cell['b']:.4f} "
                f"c={refined_cell['c']:.4f}"
            )
            print(f"  Volume: {vol:.3f} Å³, wR = {wr:.4f}")
            if temp is not None:
                print(f"  Temperature: {temp} K")

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            results.append(
                {
                    "file": fname,
                    "success": False,
                    "error": str(e),
                }
            )

    # Multi-temperature analysis
    thermal = {}
    successful = [r for r in results if r.get("success") and "temperature_K" in r]
    if len(successful) >= 2:
        temps = np.array([r["temperature_K"] for r in successful])
        vols = np.array([r["volume"] for r in successful])

        # Phase transition detection
        transitions = detect_phase_transitions(temps, vols)
        if transitions:
            print(f"\nPhase transition detected at indices: {transitions}")

        # Per-parameter thermal expansion
        free_params = CRYSTAL_SYSTEMS[crystal_system]
        for param in free_params:
            vals = np.array([r["cell"][param] for r in successful])
            fit = thermal_expansion_fit(temps, vals)
            thermal[param] = fit
            print(
                f"\nThermal expansion ({param}): "
                f"slope={fit['slope']:.6e}, R²={fit['R2']:.4f}"
            )

        # Volume thermal expansion
        vol_fit = thermal_expansion_fit(temps, vols)
        thermal["volume"] = vol_fit
        print(
            f"\nVolume thermal expansion: "
            f"slope={vol_fit['slope']:.4e} Å³/K, R²={vol_fit['R2']:.4f}"
        )

    # Output
    output = {
        "success": any(r.get("success") for r in results),
        "space_group": args.sg,
        "crystal_system": crystal_system,
        "wavelength": args.wavelength,
        "initial_cell": cell,
        "results": results,
    }
    if thermal:
        output["thermal_expansion"] = thermal
    if successful and len(successful) >= 2:
        transitions_found = detect_phase_transitions(
            np.array([r["temperature_K"] for r in successful]),
            np.array([r["volume"] for r in successful]),
        )
        if transitions_found:
            output["phase_transitions"] = transitions_found

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
