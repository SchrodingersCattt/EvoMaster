#!/usr/bin/env python3
"""
refine_lattice_pxrd.py — Local PXRD lattice parameter refinement.

Performs Pawley-type lattice parameter refinement from powder XRD data
using scipy least_squares. Supports:
- Single-pattern refinement
- Multi-temperature mode with phase transition detection
- Per-phase thermal expansion fitting

Usage:
    # Single pattern
    python3 refine_lattice_pxrd.py --data pattern.xy \\
        --space-group "Pm-3m" --cell "a=3.905,b=3.905,c=3.905" \\
        --wavelength 1.5406 -o results.json

    # Multi-temperature
    python3 refine_lattice_pxrd.py --data-dir ./patterns/ \\
        --space-group "Pm-3m" --cell "a=3.905,b=3.905,c=3.905" \\
        --wavelength 1.5406 --multi-temp -o results.json
"""

import argparse
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from scipy.optimize import least_squares
    from scipy.signal import find_peaks
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    warnings.warn("scipy not available — refinement will not work")


# ---------------------------------------------------------------------------
# Data I/O
# ---------------------------------------------------------------------------

def load_xy_data(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load 2theta-intensity data from .xy / .csv / .dat / .txt file."""
    data_lines = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('!'):
                continue
            # Try comma, semicolon, whitespace separators
            for sep in [',', ';', None]:
                parts = line.split(sep)
                if len(parts) >= 2:
                    try:
                        x = float(parts[0].strip())
                        y = float(parts[1].strip())
                        data_lines.append((x, y))
                        break
                    except ValueError:
                        continue

    if not data_lines:
        raise ValueError(f"No valid data points found in {path}")

    data = np.array(data_lines)
    return data[:, 0], data[:, 1]


def parse_cell_string(cell_str: str) -> Dict[str, float]:
    """Parse cell string like 'a=3.905,b=3.905,c=3.905,alpha=90,beta=90,gamma=90'."""
    cell = {"alpha": 90.0, "beta": 90.0, "gamma": 90.0}
    for part in cell_str.split(','):
        part = part.strip()
        if '=' in part:
            key, val = part.split('=', 1)
            cell[key.strip()] = float(val.strip())
    return cell


# ---------------------------------------------------------------------------
# Peak finding (adaptive)
# ---------------------------------------------------------------------------

def find_diffraction_peaks(
    two_theta: np.ndarray,
    intensity: np.ndarray,
    min_peaks: int = 5,
) -> np.ndarray:
    """
    Find diffraction peaks with adaptive thresholds.
    Falls back to progressively looser thresholds if too few peaks found.
    Returns array of peak 2theta positions.
    """
    if not HAS_SCIPY:
        # Simple fallback: find local maxima
        peaks = []
        for i in range(1, len(intensity) - 1):
            if intensity[i] > intensity[i-1] and intensity[i] > intensity[i+1]:
                if intensity[i] > np.mean(intensity) + 2 * np.std(intensity):
                    peaks.append(two_theta[i])
        return np.array(peaks)

    # Adaptive peak finding with decreasing thresholds
    bg = np.percentile(intensity, 10)
    intensity_bg = intensity - bg

    for factor in [1.0, 0.5, 0.25, 0.1]:
        height_thresh = factor * np.std(intensity_bg)
        prominence_thresh = factor * 0.5 * np.std(intensity_bg)
        distance = max(3, int(0.2 / np.mean(np.diff(two_theta))))  # ~0.2 deg minimum separation

        peaks_idx, properties = find_peaks(
            intensity_bg,
            height=height_thresh,
            prominence=prominence_thresh,
            distance=distance,
        )

        if len(peaks_idx) >= min_peaks:
            break

    if len(peaks_idx) == 0:
        # Last resort: just find the N highest local maxima
        from scipy.signal import argrelextrema
        maxima = argrelextrema(intensity, np.greater, order=5)[0]
        if len(maxima) > 0:
            sorted_idx = maxima[np.argsort(intensity[maxima])[::-1]]
            peaks_idx = sorted_idx[:max(min_peaks, 20)]

    return two_theta[peaks_idx]


# ---------------------------------------------------------------------------
# d-spacing calculation
# ---------------------------------------------------------------------------

def calc_d_spacing(
    h: int, k: int, l: int,
    a: float, b: float, c: float,
    alpha: float = 90.0, beta: float = 90.0, gamma: float = 90.0,
) -> float:
    """Calculate d-spacing for given hkl and cell parameters."""
    alpha_r = np.radians(alpha)
    beta_r = np.radians(beta)
    gamma_r = np.radians(gamma)

    ca, cb, cg = np.cos(alpha_r), np.cos(beta_r), np.cos(gamma_r)
    sa, sb, sg = np.sin(alpha_r), np.sin(beta_r), np.sin(gamma_r)

    V = a * b * c * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2*ca*cb*cg)

    s11 = (b * c * sa)**2
    s22 = (a * c * sb)**2
    s33 = (a * b * sg)**2
    s12 = a * b * c**2 * (ca * cb - cg)
    s23 = a**2 * b * c * (cb * cg - ca)
    s13 = a * b**2 * c * (ca * cg - cb)

    d_inv_sq = (s11 * h**2 + s22 * k**2 + s33 * l**2 +
                2 * s12 * h * k + 2 * s23 * k * l + 2 * s13 * h * l) / V**2

    if d_inv_sq <= 0:
        return 1e10
    return 1.0 / np.sqrt(d_inv_sq)


def two_theta_from_d(d: float, wavelength: float) -> float:
    """Calculate 2theta from d-spacing via Bragg's law."""
    sin_theta = wavelength / (2.0 * d)
    if abs(sin_theta) > 1.0:
        return 180.0
    return 2.0 * np.degrees(np.arcsin(sin_theta))


# ---------------------------------------------------------------------------
# HKL generation for a crystal system
# ---------------------------------------------------------------------------

def _get_crystal_system(sg_name: str) -> str:
    """Determine crystal system from space group name."""
    sg_name = sg_name.strip()
    # Very simplified lookup
    cubic = ["Pm-3m", "Fm-3m", "Im-3m", "Pm3m", "Fm3m", "Im3m",
             "Pa-3", "Ia-3", "Fd-3m", "Fd3m", "Pa3", "Ia3",
             "P2_13", "P213", "I2_13", "I213", "Ia-3d"]
    tetragonal = ["P4/mmm", "I4/mmm", "P42/mnm", "P4/nmm", "I41/amd",
                  "P4mm", "I4mm", "P-42m", "P42m"]
    hexagonal = ["P6/mmm", "P63/mmc", "P6_3/mmc", "P6mm", "P-6m2",
                 "P6_3mc", "R-3m", "R3m", "R-3c", "R3c"]
    orthorhombic = ["Pnma", "Cmcm", "Pbca", "Pmmm", "Fmmm", "Immm",
                    "Pna2_1", "Pna21", "P2_12_12_1", "P212121", "Cmc2_1",
                    "Cmca", "Ibam", "Pbcm", "Pbcn"]
    monoclinic = ["P2_1/c", "P21/c", "C2/c", "P2_1", "P21", "C2", "Cc",
                  "P2/m", "P2/c", "C2/m"]

    for sg_list, system in [(cubic, "cubic"), (tetragonal, "tetragonal"),
                             (hexagonal, "hexagonal"), (orthorhombic, "orthorhombic"),
                             (monoclinic, "monoclinic")]:
        if sg_name in sg_list or sg_name.replace(" ", "") in [s.replace(" ", "") for s in sg_list]:
            return system

    return "triclinic"


def generate_hkl_list(
    sg_name: str,
    max_index: int = 10,
    d_min: float = 0.5,
    cell: Dict[str, float] = None,
) -> List[Tuple[int, int, int]]:
    """Generate allowed HKL indices for a space group up to max_index."""
    system = _get_crystal_system(sg_name)
    hkl_list = []

    for h in range(-max_index, max_index + 1):
        for k in range(-max_index, max_index + 1):
            for l_idx in range(-max_index, max_index + 1):
                if h == 0 and k == 0 and l_idx == 0:
                    continue

                # Systematic absence rules (simplified)
                # This is approximate — full implementation would need SG tables
                allowed = True

                # Check d-spacing if cell provided
                if cell and allowed:
                    d = calc_d_spacing(
                        h, k, l_idx,
                        cell.get('a', 5), cell.get('b', 5), cell.get('c', 5),
                        cell.get('alpha', 90), cell.get('beta', 90), cell.get('gamma', 90)
                    )
                    if d < d_min:
                        allowed = False

                if allowed:
                    # Only keep unique (positive hemisphere)
                    if h > 0 or (h == 0 and k > 0) or (h == 0 and k == 0 and l_idx > 0):
                        hkl_list.append((h, k, l_idx))

    return hkl_list


# ---------------------------------------------------------------------------
# Pawley-type refinement
# ---------------------------------------------------------------------------

def pawley_refine(
    peak_positions: np.ndarray,
    sg_name: str,
    initial_cell: Dict[str, float],
    wavelength: float = 1.5406,
    max_index: int = 8,
) -> Dict:
    """
    Pawley-type lattice parameter refinement.

    Matches observed peak positions to calculated positions for allowed reflections,
    then refines cell parameters to minimize the position differences.

    Returns dict with refined cell, residuals, etc.
    """
    if not HAS_SCIPY:
        return {
            "success": False,
            "error": "scipy not available",
            "cell": initial_cell,
        }

    system = _get_crystal_system(sg_name)
    a0 = initial_cell.get('a', 5.0)
    b0 = initial_cell.get('b', a0)
    c0 = initial_cell.get('c', a0)
    alpha0 = initial_cell.get('alpha', 90.0)
    beta0 = initial_cell.get('beta', 90.0)
    gamma0 = initial_cell.get('gamma', 90.0)

    # Generate HKL list
    hkl_list = generate_hkl_list(sg_name, max_index=max_index, cell=initial_cell)

    if not hkl_list:
        return {"success": False, "error": "No HKL reflections generated", "cell": initial_cell}

    # Set up parameters based on crystal system
    if system == "cubic":
        params0 = [a0]
        def cell_from_params(p): return p[0], p[0], p[0], 90, 90, 90
        bounds_lo, bounds_hi = [a0 * 0.9], [a0 * 1.1]
    elif system == "tetragonal":
        params0 = [a0, c0]
        def cell_from_params(p): return p[0], p[0], p[1], 90, 90, 90
        bounds_lo, bounds_hi = [a0 * 0.9, c0 * 0.9], [a0 * 1.1, c0 * 1.1]
    elif system == "hexagonal":
        params0 = [a0, c0]
        def cell_from_params(p): return p[0], p[0], p[1], 90, 90, 120
        bounds_lo, bounds_hi = [a0 * 0.9, c0 * 0.9], [a0 * 1.1, c0 * 1.1]
    elif system == "orthorhombic":
        params0 = [a0, b0, c0]
        def cell_from_params(p): return p[0], p[1], p[2], 90, 90, 90
        bounds_lo = [a0 * 0.9, b0 * 0.9, c0 * 0.9]
        bounds_hi = [a0 * 1.1, b0 * 1.1, c0 * 1.1]
    elif system == "monoclinic":
        params0 = [a0, b0, c0, beta0]
        def cell_from_params(p): return p[0], p[1], p[2], 90, p[3], 90
        bounds_lo = [a0 * 0.9, b0 * 0.9, c0 * 0.9, beta0 - 5]
        bounds_hi = [a0 * 1.1, b0 * 1.1, c0 * 1.1, beta0 + 5]
    else:  # triclinic
        params0 = [a0, b0, c0, alpha0, beta0, gamma0]
        def cell_from_params(p): return p[0], p[1], p[2], p[3], p[4], p[5]
        bounds_lo = [a0*0.9, b0*0.9, c0*0.9, alpha0-5, beta0-5, gamma0-5]
        bounds_hi = [a0*1.1, b0*1.1, c0*1.1, alpha0+5, beta0+5, gamma0+5]

    def residuals(params):
        a, b, c, alpha, beta, gamma = cell_from_params(params)
        # Calculate all possible 2theta positions
        calc_2theta = []
        for h, k, l in hkl_list:
            d = calc_d_spacing(h, k, l, a, b, c, alpha, beta, gamma)
            tt = two_theta_from_d(d, wavelength)
            if 5 < tt < 160:  # reasonable range
                calc_2theta.append(tt)
        calc_2theta = np.array(sorted(set([round(t, 3) for t in calc_2theta])))

        if len(calc_2theta) == 0:
            return np.ones(len(peak_positions)) * 10

        # Match each observed peak to nearest calculated
        resid = []
        for obs_tt in peak_positions:
            diffs = np.abs(calc_2theta - obs_tt)
            min_diff = np.min(diffs)
            resid.append(min_diff)
        return np.array(resid)

    try:
        result = least_squares(
            residuals, params0,
            bounds=(bounds_lo, bounds_hi),
            method='trf',
            ftol=1e-10, xtol=1e-10,
            max_nfev=2000,
        )
        refined_params = result.x
        success = result.success
        cost = result.cost
    except Exception as e:
        refined_params = params0
        success = False
        cost = float('inf')

    a, b, c, alpha, beta, gamma = cell_from_params(refined_params)

    # Calculate volume
    alpha_r, beta_r, gamma_r = np.radians(alpha), np.radians(beta), np.radians(gamma)
    V = a * b * c * np.sqrt(
        1 - np.cos(alpha_r)**2 - np.cos(beta_r)**2 - np.cos(gamma_r)**2
        + 2 * np.cos(alpha_r) * np.cos(beta_r) * np.cos(gamma_r)
    )

    final_resid = residuals(refined_params)
    mean_resid = np.mean(final_resid)

    return {
        "success": success,
        "cell": {
            "a": round(a, 6), "b": round(b, 6), "c": round(c, 6),
            "alpha": round(alpha, 4), "beta": round(beta, 4), "gamma": round(gamma, 4),
        },
        "volume": round(V, 4),
        "crystal_system": system,
        "space_group": sg_name,
        "wavelength": wavelength,
        "n_peaks_observed": len(peak_positions),
        "n_reflections_model": len(hkl_list),
        "mean_2theta_residual_deg": round(mean_resid, 6),
        "cost": round(cost, 8),
    }


# ---------------------------------------------------------------------------
# Multi-temperature mode
# ---------------------------------------------------------------------------

def detect_phase_transition(
    temperatures: List[float],
    volumes: List[float],
    threshold: float = 0.02,
) -> List[int]:
    """
    Detect phase transition points from volume vs temperature data.
    Returns list of indices where transitions occur.
    """
    if len(volumes) < 3:
        return []

    transitions = []
    v = np.array(volumes)
    t = np.array(temperatures)

    # Look for discontinuities in dV/dT
    dv = np.diff(v)
    dt = np.diff(t)
    dt[dt == 0] = 1e-6
    dvdt = dv / dt

    if len(dvdt) >= 3:
        median_dvdt = np.median(np.abs(dvdt))
        for i in range(1, len(dvdt)):
            if abs(dvdt[i] - dvdt[i-1]) > 5 * max(median_dvdt, threshold):
                transitions.append(i)

    return transitions


def fit_thermal_expansion(
    temperatures: np.ndarray,
    values: np.ndarray,
) -> Dict:
    """
    Linear fit of lattice parameter vs temperature.
    Returns slope, intercept, R^2.
    """
    if len(temperatures) < 2:
        return {"slope": 0.0, "intercept": values[0] if len(values) > 0 else 0.0, "R2": 0.0}

    # Linear fit
    coeffs = np.polyfit(temperatures, values, 1)
    slope, intercept = coeffs
    predicted = np.polyval(coeffs, temperatures)
    ss_res = np.sum((values - predicted) ** 2)
    ss_tot = np.sum((values - np.mean(values)) ** 2)
    R2 = 1 - ss_res / max(ss_tot, 1e-20) if ss_tot > 0 else 0.0

    return {
        "slope": round(slope, 10),
        "intercept": round(intercept, 6),
        "R2": round(R2, 6),
    }


def multi_temperature_refinement(
    data_dir: str,
    sg_name: str,
    initial_cell: Dict[str, float],
    wavelength: float = 1.5406,
) -> Dict:
    """
    Refine lattice parameters for each temperature in a directory of patterns.
    Detect phase transitions and fit thermal expansion per phase.
    """
    # Find pattern files
    pattern_dir = Path(data_dir)
    pattern_files = sorted(
        [f for f in pattern_dir.glob("*") if f.suffix.lower() in
         ['.xy', '.dat', '.csv', '.txt', '.xye']],
        key=lambda f: f.stem
    )

    if not pattern_files:
        return {"success": False, "error": f"No pattern files found in {data_dir}"}

    # Extract temperatures from filenames
    results_per_temp = []
    for pf in pattern_files:
        # Try to extract temperature from filename
        temp_match = re.search(r'(\d+)\s*[Kk]', pf.stem)
        if not temp_match:
            temp_match = re.search(r'[Tt]?(\d+)', pf.stem)
        temp = float(temp_match.group(1)) if temp_match else len(results_per_temp)

        print(f"  Processing {pf.name} (T={temp})...")
        two_theta, intensity = load_xy_data(str(pf))
        peaks = find_diffraction_peaks(two_theta, intensity)

        if len(peaks) < 3:
            print(f"    WARNING: Only {len(peaks)} peaks found, skipping")
            continue

        result = pawley_refine(peaks, sg_name, initial_cell, wavelength)
        result["temperature"] = temp
        result["file"] = pf.name
        results_per_temp.append(result)

        # Update initial cell for next temperature (for continuity)
        if result["success"]:
            initial_cell = result["cell"]

    if not results_per_temp:
        return {"success": False, "error": "No successful refinements"}

    # Sort by temperature
    results_per_temp.sort(key=lambda r: r["temperature"])

    temperatures = [r["temperature"] for r in results_per_temp]
    volumes = [r["volume"] for r in results_per_temp]

    # Detect phase transitions
    transitions = detect_phase_transition(temperatures, volumes)

    # Split into phases and fit thermal expansion
    phase_boundaries = [0] + transitions + [len(temperatures)]
    phases = []
    for i in range(len(phase_boundaries) - 1):
        start = phase_boundaries[i]
        end = phase_boundaries[i + 1]
        phase_temps = np.array(temperatures[start:end])
        phase_vols = np.array(volumes[start:end])

        if len(phase_temps) < 2:
            continue

        # Fit volume expansion
        vol_fit = fit_thermal_expansion(phase_temps, phase_vols)

        # Fit each lattice parameter
        param_fits = {}
        for param in ['a', 'b', 'c']:
            vals = np.array([r["cell"][param] for r in results_per_temp[start:end]])
            param_fits[param] = fit_thermal_expansion(phase_temps, vals)

        phases.append({
            "phase_index": i + 1,
            "temperature_range": [float(phase_temps[0]), float(phase_temps[-1])],
            "n_points": len(phase_temps),
            "volume_fit": vol_fit,
            "lattice_parameter_fits": param_fits,
        })

    return {
        "success": True,
        "n_temperatures": len(results_per_temp),
        "n_phases": len(phases),
        "transition_temperatures": [temperatures[t] for t in transitions],
        "per_temperature": results_per_temp,
        "phases": phases,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PXRD lattice parameter refinement")
    parser.add_argument("--data", help="Single PXRD pattern file")
    parser.add_argument("--data-dir", help="Directory with multi-temperature patterns")
    parser.add_argument("--space-group", "--sg", required=True, help="Space group")
    parser.add_argument("--cell", required=True,
                       help="Initial cell: 'a=X,b=Y,c=Z[,alpha=A,beta=B,gamma=G]'")
    parser.add_argument("--wavelength", type=float, default=1.5406,
                       help="X-ray wavelength (default: 1.5406 Å, Cu Kα)")
    parser.add_argument("--multi-temp", action="store_true",
                       help="Multi-temperature mode")
    parser.add_argument("--output", "-o", default="results.json",
                       help="Output JSON file")
    args = parser.parse_args()

    initial_cell = parse_cell_string(args.cell)
    print(f"=== PXRD Lattice Refinement ===")
    print(f"Space group: {args.space_group}")
    print(f"Initial cell: {initial_cell}")
    print(f"Wavelength: {args.wavelength} Å")

    if args.data_dir or args.multi_temp:
        data_dir = args.data_dir or (os.path.dirname(args.data) if args.data else ".")
        print(f"\nMulti-temperature mode: {data_dir}")
        result = multi_temperature_refinement(
            data_dir, args.space_group, initial_cell, args.wavelength
        )
    elif args.data:
        print(f"\nSingle pattern: {args.data}")
        two_theta, intensity = load_xy_data(args.data)
        print(f"  {len(two_theta)} data points loaded")
        print(f"  2θ range: {two_theta[0]:.2f}° — {two_theta[-1]:.2f}°")

        peaks = find_diffraction_peaks(two_theta, intensity)
        print(f"  {len(peaks)} peaks found")

        if len(peaks) < 3:
            print("ERROR: Too few peaks for refinement")
            result = {"success": False, "error": "Too few peaks", "cell": initial_cell}
        else:
            result = pawley_refine(
                peaks, args.space_group, initial_cell, args.wavelength
            )
    else:
        print("ERROR: Provide --data or --data-dir")
        sys.exit(1)

    # Save results
    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to: {args.output}")

    if result.get("success"):
        if "per_temperature" in result:
            print(f"\nRefined {result['n_temperatures']} temperatures, "
                  f"{result['n_phases']} phase(s)")
            if result.get("transition_temperatures"):
                print(f"Phase transitions at: {result['transition_temperatures']}")
            for phase in result.get("phases", []):
                print(f"\nPhase {phase['phase_index']} "
                      f"({phase['temperature_range'][0]}-{phase['temperature_range'][1]} K):")
                vf = phase["volume_fit"]
                print(f"  Volume: slope={vf['slope']:.6f} Å³/K, R²={vf['R2']:.4f}")
                for p, pf in phase["lattice_parameter_fits"].items():
                    print(f"  {p}: slope={pf['slope']:.8f} Å/K, R²={pf['R2']:.4f}")
        else:
            cell = result["cell"]
            print(f"\nRefined cell:")
            print(f"  a={cell['a']:.6f} b={cell['b']:.6f} c={cell['c']:.6f}")
            print(f"  α={cell['alpha']:.4f} β={cell['beta']:.4f} γ={cell['gamma']:.4f}")
            print(f"  V={result['volume']:.4f} Å³")
            print(f"  Mean 2θ residual: {result['mean_2theta_residual_deg']:.4f}°")
    else:
        print(f"\nRefinement failed: {result.get('error', 'unknown')}")

    print(f"\n=== Done ===")


if __name__ == "__main__":
    main()
