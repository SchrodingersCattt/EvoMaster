#!/usr/bin/env python3
"""
refine_lattice_pxrd.py — Pawley-type lattice parameter refinement from PXRD data.

Supports single-pattern and multi-temperature modes with automatic phase
transition detection and per-phase thermal expansion fitting.

Usage:
  # Single pattern
  python refine_lattice_pxrd.py --data pattern.xy --sg "P21/c" \\
      --cell "a=10.5,b=12.3,c=8.7,beta=105.2" --wavelength 1.5406 -o result.json

  # Multi-temperature directory (files named *_300K.xy, *_350K.xy, etc.)
  python refine_lattice_pxrd.py --data ./vt_patterns/ --sg "P21/c" \\
      --cell "a=10.5,b=12.3,c=8.7,beta=105.2" --wavelength 1.5406 \\
      --multi-temp -o result.json
"""
import argparse
import glob
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks

# Add script directory to path for sibling imports
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ──────────── Data Loading ────────────

def load_xy_pattern(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load 2θ vs intensity from .xy, .dat, .csv, or similar text file."""
    two_theta = []
    intensity = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('!'):
                continue
            # Handle comma or space/tab separation
            parts = re.split(r'[,\s\t]+', line)
            if len(parts) >= 2:
                try:
                    tt = float(parts[0])
                    ii = float(parts[1])
                    two_theta.append(tt)
                    intensity.append(ii)
                except ValueError:
                    continue
    if not two_theta:
        raise ValueError(f"No data loaded from {path}")
    return np.array(two_theta), np.array(intensity)


def extract_temperature(filename: str) -> Optional[float]:
    """Try to extract temperature from filename (e.g. pattern_300K.xy → 300.0)."""
    m = re.search(r'(\d+(?:\.\d+)?)\s*[Kk]', filename)
    if m:
        return float(m.group(1))
    # Try bare number patterns
    m = re.search(r'_(\d+)\.', filename)
    if m:
        val = float(m.group(1))
        if 50 <= val <= 2000:  # plausible temperature
            return val
    return None


# ──────────── Peak Finding ────────────

def find_peaks_adaptive(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    min_peaks: int = 5,
) -> np.ndarray:
    """
    Find diffraction peaks with adaptive thresholds.
    Returns array of peak 2θ positions.
    """
    signal_range = np.max(intensity) - np.min(intensity)
    noise_floor = np.min(intensity)

    # Primary attempt
    height = noise_floor + 0.05 * signal_range
    prominence = 0.03 * signal_range
    peaks, props = find_peaks(intensity, height=height, prominence=prominence, distance=3)

    if len(peaks) < min_peaks:
        # Retry with looser criteria
        height = noise_floor + 0.025 * signal_range
        prominence = 0.015 * signal_range
        peaks, props = find_peaks(intensity, height=height, prominence=prominence, distance=3)

    if len(peaks) < min_peaks:
        # Last resort
        height = noise_floor + 0.01 * signal_range
        prominence = 0.005 * signal_range
        peaks, props = find_peaks(intensity, height=height, prominence=prominence, distance=2)

    # Refine peak positions with parabolic interpolation
    refined_positions = []
    for p in peaks:
        if 1 <= p < len(intensity) - 1:
            y0, y1, y2 = intensity[p-1], intensity[p], intensity[p+1]
            denom = 2 * (2 * y1 - y0 - y2)
            if abs(denom) > 1e-10:
                shift = (y0 - y2) / denom
                shift = max(-0.5, min(0.5, shift))  # clamp
                refined_pos = two_theta[p] + shift * (two_theta[1] - two_theta[0])
            else:
                refined_pos = two_theta[p]
        else:
            refined_pos = two_theta[p]
        refined_positions.append(refined_pos)

    return np.array(refined_positions)


# ──────────── Lattice Calculations ────────────

def parse_cell_string(cell_str: str) -> Dict[str, float]:
    """Parse cell string like 'a=10.5,b=12.3,c=8.7,alpha=90,beta=105.2,gamma=90'."""
    cell = {"a": 10.0, "b": 10.0, "c": 10.0, "alpha": 90.0, "beta": 90.0, "gamma": 90.0}
    for part in cell_str.split(","):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.strip().lower()
            if key in cell:
                cell[key] = float(val.strip())
    return cell


def calc_d_spacing(h: int, k: int, l: int, cell: Dict[str, float]) -> float:
    """Calculate d-spacing for reflection (h,k,l)."""
    a, b, c = cell["a"], cell["b"], cell["c"]
    al = math.radians(cell["alpha"])
    be = math.radians(cell["beta"])
    ga = math.radians(cell["gamma"])

    V = a * b * c * math.sqrt(
        1 - math.cos(al)**2 - math.cos(be)**2 - math.cos(ga)**2
        + 2 * math.cos(al) * math.cos(be) * math.cos(ga)
    )

    s11 = (b * c * math.sin(al))**2
    s22 = (a * c * math.sin(be))**2
    s33 = (a * b * math.sin(ga))**2
    s12 = a * b * c**2 * (math.cos(al) * math.cos(be) - math.cos(ga))
    s13 = a * b**2 * c * (math.cos(ga) * math.cos(al) - math.cos(be))
    s23 = a**2 * b * c * (math.cos(be) * math.cos(ga) - math.cos(al))

    inv_d2 = (s11 * h**2 + s22 * k**2 + s33 * l**2 +
              2 * s12 * h * k + 2 * s13 * h * l + 2 * s23 * k * l) / V**2

    if inv_d2 <= 0:
        return 1e10
    return 1.0 / math.sqrt(inv_d2)


def d_to_two_theta(d: float, wavelength: float) -> float:
    """Convert d-spacing to 2θ (degrees)."""
    sin_theta = wavelength / (2.0 * d)
    if abs(sin_theta) > 1.0:
        return 180.0  # unreachable
    return 2.0 * math.degrees(math.asin(sin_theta))


def generate_hkl_list(
    cell: Dict[str, float],
    wavelength: float,
    two_theta_max: float = 80.0,
    h_max: int = 15,
) -> List[Tuple[int, int, int, float]]:
    """Generate list of (h,k,l,2θ) within the measured range."""
    reflections = []
    d_min = wavelength / (2.0 * math.sin(math.radians(two_theta_max / 2.0)))
    for h in range(-h_max, h_max + 1):
        for k in range(-h_max, h_max + 1):
            for l in range(-h_max, h_max + 1):
                if h == 0 and k == 0 and l == 0:
                    continue
                # Only positive hemisphere
                if h < 0:
                    continue
                if h == 0 and k < 0:
                    continue
                if h == 0 and k == 0 and l < 0:
                    continue
                d = calc_d_spacing(h, k, l, cell)
                if d < d_min:
                    continue
                tt = d_to_two_theta(d, wavelength)
                if tt < two_theta_max:
                    reflections.append((h, k, l, tt))
    reflections.sort(key=lambda x: x[3])
    return reflections


# ──────────── Pawley Refinement ────────────

def refine_cell(
    obs_peaks: np.ndarray,
    cell: Dict[str, float],
    wavelength: float,
    sg_system: str = "monoclinic",
) -> Tuple[Dict[str, float], float, List[dict]]:
    """
    Pawley-type refinement: adjust cell parameters to match observed peak positions.
    Returns (refined_cell, weighted_R, matched_reflections).
    """
    two_theta_max = max(obs_peaks) + 2.0

    # Generate candidate reflections
    hkl_list = generate_hkl_list(cell, wavelength, two_theta_max)
    if not hkl_list:
        return cell, 999.0, []

    # Match observed peaks to calculated ones
    def match_peaks(test_cell):
        matched = []
        calc_peaks = []
        for h, k, l, _ in hkl_list:
            d = calc_d_spacing(h, k, l, test_cell)
            tt = d_to_two_theta(d, wavelength)
            calc_peaks.append((h, k, l, tt))

        used_obs = set()
        for h, k, l, tt_calc in sorted(calc_peaks, key=lambda x: x[3]):
            best_dist = 999
            best_idx = -1
            for i, tt_obs in enumerate(obs_peaks):
                if i in used_obs:
                    continue
                dist = abs(tt_obs - tt_calc)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
            # Match within tolerance
            for tol_mult in [1.0, 2.0, 4.0]:
                tol = 0.15 * tol_mult
                if best_dist < tol and best_idx not in used_obs:
                    matched.append({
                        "h": h, "k": k, "l": l,
                        "tt_obs": obs_peaks[best_idx],
                        "tt_calc": tt_calc,
                        "diff": obs_peaks[best_idx] - tt_calc,
                    })
                    used_obs.add(best_idx)
                    break
        return matched

    # Determine which parameters to refine based on crystal system
    if sg_system in ("cubic",):
        param_names = ["a"]
    elif sg_system in ("tetragonal",):
        param_names = ["a", "c"]
    elif sg_system in ("orthorhombic",):
        param_names = ["a", "b", "c"]
    elif sg_system in ("hexagonal", "trigonal"):
        param_names = ["a", "c"]
    elif sg_system in ("monoclinic",):
        param_names = ["a", "b", "c", "beta"]
    else:  # triclinic
        param_names = ["a", "b", "c", "alpha", "beta", "gamma"]

    x0 = [cell[p] for p in param_names]

    def residuals(params):
        test_cell = dict(cell)
        for name, val in zip(param_names, params):
            test_cell[name] = val
            # Enforce symmetry constraints
            if sg_system == "cubic":
                test_cell["b"] = test_cell["c"] = val
            elif sg_system == "tetragonal" and name == "a":
                test_cell["b"] = val
            elif sg_system in ("hexagonal", "trigonal") and name == "a":
                test_cell["b"] = val

        matched = match_peaks(test_cell)
        if not matched:
            return np.ones(len(obs_peaks)) * 10.0
        return np.array([m["diff"] for m in matched])

    result = least_squares(
        residuals, x0,
        method='trf', ftol=1e-6, xtol=1e-6,
        max_nfev=500,
    )

    refined_cell = dict(cell)
    for name, val in zip(param_names, result.x):
        refined_cell[name] = val
        if sg_system == "cubic":
            refined_cell["b"] = refined_cell["c"] = val
        elif sg_system == "tetragonal" and name == "a":
            refined_cell["b"] = val
        elif sg_system in ("hexagonal", "trigonal") and name == "a":
            refined_cell["b"] = val

    matched = match_peaks(refined_cell)

    # Calculate wR
    if matched:
        diffs = np.array([m["diff"] for m in matched])
        obs_tt = np.array([m["tt_obs"] for m in matched])
        wR = np.sqrt(np.sum(diffs**2) / np.sum(obs_tt**2)) if np.sum(obs_tt**2) > 0 else 999
    else:
        wR = 999

    return refined_cell, wR, matched


# ──────────── Thermal Expansion Analysis ────────────

def detect_phase_transitions(
    temps: np.ndarray,
    volumes: np.ndarray,
    threshold: float = 0.03,
) -> List[int]:
    """Detect phase transitions from volume discontinuities."""
    if len(temps) < 4:
        return []
    transitions = []
    dV = np.diff(volumes) / volumes[:-1]
    for i in range(len(dV)):
        if abs(dV[i]) > threshold:
            transitions.append(i + 1)  # index in temps where transition occurs
    return transitions


def fit_thermal_expansion(
    temps: np.ndarray,
    values: np.ndarray,
) -> Dict[str, float]:
    """Fit linear thermal expansion: value = slope * T + intercept."""
    if len(temps) < 2:
        return {"slope": 0.0, "intercept": values[0] if len(values) > 0 else 0.0, "R2": 0.0}
    coeffs = np.polyfit(temps, values, 1)
    slope, intercept = coeffs
    predicted = np.polyval(coeffs, temps)
    ss_res = np.sum((values - predicted)**2)
    ss_tot = np.sum((values - np.mean(values))**2)
    R2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope": slope, "intercept": intercept, "R2": R2}


# ──────────── Main ────────────

def main():
    parser = argparse.ArgumentParser(description="PXRD Pawley lattice refinement")
    parser.add_argument("--data", required=True, help="Pattern file or directory for VT-PXRD")
    parser.add_argument("--sg", "--space-group", required=True, help="Space group")
    parser.add_argument("--cell", required=True, help="Initial cell: 'a=X,b=Y,c=Z,beta=B'")
    parser.add_argument("--wavelength", type=float, default=1.5406, help="X-ray wavelength (Å)")
    parser.add_argument("--multi-temp", action="store_true", help="Multi-temperature mode")
    parser.add_argument("--crystal-system", default=None, help="Crystal system override")
    parser.add_argument("--output", "-o", default="result.json", help="Output JSON")
    parser.add_argument("--debug-plot", help="Directory for debug plots")
    args = parser.parse_args()

    print("=" * 60)
    print("PXRD Pawley Lattice Refinement")
    print("=" * 60)

    initial_cell = parse_cell_string(args.cell)
    print(f"\nInitial cell: a={initial_cell['a']:.4f} b={initial_cell['b']:.4f} "
          f"c={initial_cell['c']:.4f}")
    print(f"              α={initial_cell['alpha']:.2f} β={initial_cell['beta']:.2f} "
          f"γ={initial_cell['gamma']:.2f}")
    print(f"Space group: {args.sg}")
    print(f"Wavelength: {args.wavelength:.5f} Å")

    # Determine crystal system
    from solve_refine_scxrd_lib import get_sg_number, crystal_system as cs_func
    sg_num = get_sg_number(args.sg)
    cs = args.crystal_system or cs_func(sg_num)
    print(f"Crystal system: {cs}")

    # Collect patterns
    patterns: List[Tuple[Optional[float], str]] = []
    data_path = Path(args.data)

    if data_path.is_dir():
        files = sorted(glob.glob(str(data_path / "*")))
        for fp in files:
            if Path(fp).suffix.lower() in (".xy", ".dat", ".csv", ".txt", ".raw", ".asc"):
                temp = extract_temperature(Path(fp).name)
                patterns.append((temp, fp))
        if not patterns:
            print("ERROR: No pattern files found in directory")
            sys.exit(1)
        # Sort by temperature
        patterns.sort(key=lambda x: (x[0] is None, x[0] or 0))
    else:
        temp = extract_temperature(data_path.name)
        patterns.append((temp, str(data_path)))

    print(f"\n{len(patterns)} pattern(s) to process")

    # Process each pattern
    results = []
    for temp, pattern_path in patterns:
        print(f"\n--- Processing: {Path(pattern_path).name}" +
              (f" (T={temp:.0f} K)" if temp else "") + " ---")

        two_theta, intensity = load_xy_pattern(pattern_path)
        print(f"  Data range: {two_theta[0]:.2f}° – {two_theta[-1]:.2f}°, {len(two_theta)} points")

        peaks = find_peaks_adaptive(two_theta, intensity)
        print(f"  Found {len(peaks)} peaks")

        if len(peaks) < 3:
            print("  WARNING: Too few peaks, skipping")
            results.append({
                "temperature": temp,
                "file": Path(pattern_path).name,
                "success": False,
                "error": "too_few_peaks",
            })
            continue

        refined_cell, wR, matched = refine_cell(
            peaks, initial_cell, args.wavelength, cs
        )

        V = refined_cell["a"] * refined_cell["b"] * refined_cell["c"]
        # Approximate volume (ignoring angles for simplicity in reporting)
        al = math.radians(refined_cell["alpha"])
        be = math.radians(refined_cell["beta"])
        ga = math.radians(refined_cell["gamma"])
        V_true = refined_cell["a"] * refined_cell["b"] * refined_cell["c"] * math.sqrt(
            1 - math.cos(al)**2 - math.cos(be)**2 - math.cos(ga)**2
            + 2 * math.cos(al) * math.cos(be) * math.cos(ga)
        )

        print(f"  Refined: a={refined_cell['a']:.4f} b={refined_cell['b']:.4f} "
              f"c={refined_cell['c']:.4f}")
        if cs == "monoclinic":
            print(f"           β={refined_cell['beta']:.3f}")
        elif cs == "triclinic":
            print(f"           α={refined_cell['alpha']:.3f} β={refined_cell['beta']:.3f} "
                  f"γ={refined_cell['gamma']:.3f}")
        print(f"  Volume: {V_true:.2f} Å³")
        print(f"  wR = {wR:.6f}, matched {len(matched)}/{len(peaks)} peaks")

        success = wR < 0.20
        result = {
            "temperature": temp,
            "file": Path(pattern_path).name,
            "success": success,
            "cell": {
                "a": round(refined_cell["a"], 5),
                "b": round(refined_cell["b"], 5),
                "c": round(refined_cell["c"], 5),
                "alpha": round(refined_cell["alpha"], 4),
                "beta": round(refined_cell["beta"], 4),
                "gamma": round(refined_cell["gamma"], 4),
            },
            "volume": round(V_true, 2),
            "wR": round(wR, 6),
            "n_peaks_observed": len(peaks),
            "n_peaks_matched": len(matched),
        }
        results.append(result)

        # Use refined cell as starting point for next temperature
        initial_cell = dict(refined_cell)

    # ─── Multi-temperature analysis ───
    thermal_analysis = None
    if args.multi_temp and len(results) > 2:
        temps = np.array([r["temperature"] for r in results if r.get("success") and r["temperature"] is not None])
        vols = np.array([r["volume"] for r in results if r.get("success") and r["temperature"] is not None])
        a_vals = np.array([r["cell"]["a"] for r in results if r.get("success") and r["temperature"] is not None])
        b_vals = np.array([r["cell"]["b"] for r in results if r.get("success") and r["temperature"] is not None])
        c_vals = np.array([r["cell"]["c"] for r in results if r.get("success") and r["temperature"] is not None])

        if len(temps) > 2:
            # Detect phase transitions
            transitions = detect_phase_transitions(temps, vols)

            # Split into phases
            phase_boundaries = [0] + transitions + [len(temps)]
            phases = []
            for i in range(len(phase_boundaries) - 1):
                start, end = phase_boundaries[i], phase_boundaries[i+1]
                if end - start < 2:
                    continue
                t_phase = temps[start:end]
                v_phase = vols[start:end]
                a_phase = a_vals[start:end]
                b_phase = b_vals[start:end]
                c_phase = c_vals[start:end]

                phase_info = {
                    "temperature_range": [float(t_phase[0]), float(t_phase[-1])],
                    "volume_expansion": fit_thermal_expansion(t_phase, v_phase),
                    "a_expansion": fit_thermal_expansion(t_phase, a_phase),
                    "b_expansion": fit_thermal_expansion(t_phase, b_phase),
                    "c_expansion": fit_thermal_expansion(t_phase, c_phase),
                }
                phases.append(phase_info)

            thermal_analysis = {
                "n_phases": len(phases),
                "transition_temperatures": [float(temps[t]) for t in transitions] if transitions else [],
                "phases": phases,
            }

            print(f"\n{'='*60}")
            print("Thermal Expansion Analysis")
            print(f"{'='*60}")
            print(f"  Detected {len(phases)} phase(s)")
            if transitions:
                print(f"  Transition(s) at: {', '.join(f'{temps[t]:.0f} K' for t in transitions)}")
            for i, phase in enumerate(phases):
                print(f"\n  Phase {i+1} ({phase['temperature_range'][0]:.0f}–{phase['temperature_range'][1]:.0f} K):")
                ve = phase["volume_expansion"]
                print(f"    Volume: slope={ve['slope']:.4f} Å³/K, R²={ve['R2']:.4f}")

    # ─── Write output ───
    output = {
        "success": any(r.get("success") for r in results),
        "space_group": args.sg,
        "wavelength": args.wavelength,
        "crystal_system": cs,
        "results": results,
    }
    if thermal_analysis:
        output["thermal_analysis"] = thermal_analysis

    with open(args.output, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
