#!/usr/bin/env python3
"""
refine_lattice_pxrd.py — Pawley-type lattice parameter refinement from PXRD data.

Extracts lattice parameters from powder XRD data by:
  1. Peak finding (scipy)
  2. Miller index assignment (crystal-system-aware)
  3. Least-squares refinement of lattice parameters against peak positions
  4. Optional multi-temperature analysis with thermal expansion & phase transition detection

Dependencies: numpy, scipy (standard scientific Python).

Usage:
  # Single pattern:
  python refine_lattice_pxrd.py --file data.xy --crystal-system tetragonal \\
    --initial-params "a=10.8,c=6.5" --wavelength 1.5406

  # Multi-temperature:
  python refine_lattice_pxrd.py --dir /data/ --crystal-system tetragonal \\
    --initial-params "a=10.8,c=6.5" --wavelength 1.5406 --multi-temp

  # With explicit temperature regex:
  python refine_lattice_pxrd.py --dir /data/ --crystal-system tetragonal \\
    --initial-params "a=10.8,c=6.5" --multi-temp --temp-pattern "(\\d+)K"

Output: JSON with refined parameters, volume, and statistics.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks


# ─── Crystal system parameter names ──────────────────────────────────
PARAM_NAMES = {
    "cubic":        ["a"],
    "tetragonal":   ["a", "c"],
    "hexagonal":    ["a", "c"],
    "orthorhombic": ["a", "b", "c"],
    "monoclinic":   ["a", "b", "c", "beta"],
    "triclinic":    ["a", "b", "c", "alpha", "beta", "gamma"],
}


# ─── d-spacing calculation ───────────────────────────────────────────
def calc_d_inv_sq(h, k, l, params, crystal_system):
    """Calculate 1/d² for given (h,k,l) and lattice parameters."""
    if crystal_system == "cubic":
        a = params[0]
        return (h**2 + k**2 + l**2) / a**2
    elif crystal_system == "tetragonal":
        a, c = params[0], params[1]
        return (h**2 + k**2) / a**2 + l**2 / c**2
    elif crystal_system == "hexagonal":
        a, c = params[0], params[1]
        return 4.0 / 3.0 * (h**2 + h * k + k**2) / a**2 + l**2 / c**2
    elif crystal_system == "orthorhombic":
        a, b, c = params[0], params[1], params[2]
        return h**2 / a**2 + k**2 / b**2 + l**2 / c**2
    elif crystal_system == "monoclinic":
        a, b, c, beta_deg = params[0], params[1], params[2], params[3]
        beta = np.radians(beta_deg)
        sb2 = np.sin(beta) ** 2
        cb = np.cos(beta)
        return (h**2 / (a**2 * sb2)
                + k**2 / b**2
                + l**2 / (c**2 * sb2)
                - 2 * h * l * cb / (a * c * sb2))
    elif crystal_system == "triclinic":
        a, b, c = params[0], params[1], params[2]
        al, be, ga = np.radians(params[3]), np.radians(params[4]), np.radians(params[5])
        ca, cb, cg = np.cos(al), np.cos(be), np.cos(ga)
        sa, sb, sg = np.sin(al), np.sin(be), np.sin(ga)
        V = a * b * c * np.sqrt(1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg)
        s11 = (b * c * sa) ** 2
        s22 = (a * c * sb) ** 2
        s33 = (a * b * sg) ** 2
        s12 = a * b * c**2 * (ca * cb - cg)
        s23 = a**2 * b * c * (cb * cg - ca)
        s13 = a * b**2 * c * (cg * ca - cb)
        return (s11 * h**2 + s22 * k**2 + s33 * l**2
                + 2 * s12 * h * k + 2 * s23 * k * l + 2 * s13 * h * l) / V**2
    raise ValueError(f"Unknown crystal system: {crystal_system}")


def calc_two_theta(d_inv_sq, wavelength):
    """Calculate 2θ (degrees) from 1/d² and wavelength."""
    sin_theta = wavelength * np.sqrt(max(d_inv_sq, 0)) / 2.0
    if sin_theta > 1.0:
        return None
    return np.degrees(2 * np.arcsin(sin_theta))


def calc_volume(params, crystal_system):
    """Calculate unit cell volume (ų) from lattice parameters."""
    if crystal_system == "cubic":
        return params[0] ** 3
    elif crystal_system == "tetragonal":
        return params[0] ** 2 * params[1]
    elif crystal_system == "hexagonal":
        return params[0] ** 2 * params[1] * np.sqrt(3) / 2
    elif crystal_system == "orthorhombic":
        return params[0] * params[1] * params[2]
    elif crystal_system == "monoclinic":
        return params[0] * params[1] * params[2] * np.sin(np.radians(params[3]))
    elif crystal_system == "triclinic":
        a, b, c = params[:3]
        al, be, ga = np.radians(params[3]), np.radians(params[4]), np.radians(params[5])
        return a * b * c * np.sqrt(
            1 - np.cos(al) ** 2 - np.cos(be) ** 2 - np.cos(ga) ** 2
            + 2 * np.cos(al) * np.cos(be) * np.cos(ga)
        )
    return 0.0


# ─── Miller index generation ────────────────────────────────────────
def generate_hkl(max_index=10):
    """Generate unique Miller indices (Friedel-pair reduced)."""
    result = []
    seen = set()
    for h in range(-max_index, max_index + 1):
        for k in range(-max_index, max_index + 1):
            for l in range(-max_index, max_index + 1):
                if h == 0 and k == 0 and l == 0:
                    continue
                key = (h, k, l) if (h, k, l) > (-h, -k, -l) else (-h, -k, -l)
                if key not in seen:
                    seen.add(key)
                    result.append(key)
    return result


# ─── Peak finding ────────────────────────────────────────────────────
def read_pxrd_data(filepath):
    """Read PXRD data from text format (XY/CSV/DAT/TSV).
    Returns (two_theta, intensity) arrays."""
    data = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            parts = re.split(r"[,\t\s]+", line)
            try:
                vals = [float(p) for p in parts if p]
                if len(vals) >= 2:
                    data.append((vals[0], vals[1]))
            except ValueError:
                continue
    if not data:
        raise ValueError(f"No numeric data found in {filepath}")
    arr = np.array(data)
    return arr[:, 0], arr[:, 1]


def find_pxrd_peaks(two_theta, intensity, min_prominence=None, min_height=None):
    """Find peaks in PXRD pattern. Returns (peak_positions, prominences)."""
    if min_prominence is None:
        min_prominence = max(np.std(intensity) * 1.5, np.median(intensity) * 0.05)
    if min_height is None:
        min_height = np.mean(intensity) + np.std(intensity) * 2

    indices, props = find_peaks(
        intensity, prominence=min_prominence, height=min_height, distance=3
    )

    # Refine positions with parabolic interpolation
    positions = []
    for idx in indices:
        if 0 < idx < len(two_theta) - 1:
            y0, y1, y2 = intensity[idx - 1], intensity[idx], intensity[idx + 1]
            denom = 2 * (y0 - 2 * y1 + y2)
            if abs(denom) > 1e-10:
                shift = (y0 - y2) / denom
                dx = (two_theta[min(idx + 1, len(two_theta) - 1)]
                      - two_theta[max(idx - 1, 0)]) / 2
                positions.append(two_theta[idx] + shift * dx)
            else:
                positions.append(two_theta[idx])
        else:
            positions.append(two_theta[idx])

    prom = props.get("prominences", np.ones(len(indices)))
    return np.array(positions), prom


# ─── Peak matching ───────────────────────────────────────────────────
def match_peaks_to_hkl(obs_peaks, hkl_list, params, crystal_system, wavelength,
                       tol=0.3, two_theta_max=160):
    """Match observed peaks to calculated HKL positions."""
    # Build calculated peak table
    calc_list = []
    for hkl in hkl_list:
        d2 = calc_d_inv_sq(*hkl, params, crystal_system)
        if d2 <= 0:
            continue
        tt = calc_two_theta(d2, wavelength)
        if tt is not None and 3 < tt < two_theta_max:
            calc_list.append((tt, hkl))
    calc_list.sort(key=lambda x: x[0])

    matches = []
    used_calc = set()
    for obs_tt in sorted(obs_peaks):
        best_diff = tol
        best_idx = None
        for ci, (calc_tt, hkl) in enumerate(calc_list):
            if ci in used_calc:
                continue
            diff = abs(obs_tt - calc_tt)
            if diff < best_diff:
                best_diff = diff
                best_idx = ci
        if best_idx is not None:
            used_calc.add(best_idx)
            matches.append((obs_tt, calc_list[best_idx][1]))
    return matches


# ─── Least-squares refinement ────────────────────────────────────────
def residuals_func(params_vec, obs_peaks, hkl_assigned, crystal_system, wavelength):
    """Residuals for scipy.optimize.least_squares."""
    res = []
    for obs_tt, hkl in zip(obs_peaks, hkl_assigned):
        d2 = calc_d_inv_sq(*hkl, params_vec, crystal_system)
        if d2 <= 0:
            res.append(5.0)
            continue
        calc_tt = calc_two_theta(d2, wavelength)
        if calc_tt is None:
            res.append(5.0)
            continue
        res.append(obs_tt - calc_tt)
    return np.array(res)


def refine_lattice(obs_peaks, hkl_assigned, initial_params, crystal_system,
                   wavelength):
    """Refine lattice parameters. Returns (params, sigma, cost, success)."""
    result = least_squares(
        residuals_func, initial_params,
        args=(obs_peaks, hkl_assigned, crystal_system, wavelength),
        method="lm", ftol=1e-12, xtol=1e-12, max_nfev=2000,
    )
    # Estimate parameter uncertainties
    n_obs = len(obs_peaks)
    n_par = len(initial_params)
    sigmas = np.zeros(n_par)
    if n_obs > n_par and result.jac is not None:
        s_sq = np.sum(result.fun ** 2) / (n_obs - n_par)
        try:
            cov = s_sq * np.linalg.inv(result.jac.T @ result.jac)
            sigmas = np.sqrt(np.maximum(np.diag(cov), 0))
        except np.linalg.LinAlgError:
            pass
    return result.x, sigmas, float(result.cost), bool(result.success)


# ─── Single-pattern processing ───────────────────────────────────────
def process_single(filepath, crystal_system, initial_params, wavelength,
                   max_index=10, tol=0.3):
    """Process one PXRD file and return a result dict."""
    two_theta, intensity = read_pxrd_data(filepath)
    peaks, proms = find_pxrd_peaks(two_theta, intensity)

    if len(peaks) < len(initial_params):
        return {"success": False, "error": f"Too few peaks ({len(peaks)})",
                "file": str(filepath)}

    hkl_list = generate_hkl(max_index)

    # Match → refine → re-match → refine (two rounds)
    matches = match_peaks_to_hkl(peaks, hkl_list, initial_params, crystal_system,
                                 wavelength, tol)
    if len(matches) < len(initial_params):
        matches = match_peaks_to_hkl(peaks, hkl_list, initial_params,
                                     crystal_system, wavelength, tol * 2)
    if len(matches) < len(initial_params):
        return {"success": False,
                "error": f"Too few matched peaks ({len(matches)})",
                "file": str(filepath)}

    obs_arr = np.array([m[0] for m in matches])
    hkl_arr = [m[1] for m in matches]
    refined, sigma, cost, ok = refine_lattice(obs_arr, hkl_arr, initial_params,
                                              crystal_system, wavelength)

    # Second round with refined params
    matches2 = match_peaks_to_hkl(peaks, hkl_list, refined, crystal_system,
                                  wavelength, tol)
    if len(matches2) >= len(matches):
        obs2 = np.array([m[0] for m in matches2])
        hkl2 = [m[1] for m in matches2]
        ref2, sig2, cost2, ok2 = refine_lattice(obs2, hkl2, refined,
                                                crystal_system, wavelength)
        if cost2 <= cost * 1.1:
            refined, sigma, cost = ref2, sig2, cost2
            matches = matches2

    # Build result
    names = PARAM_NAMES[crystal_system]
    result = {"success": True, "file": str(filepath)}
    for i, name in enumerate(names):
        result[name] = round(float(refined[i]), 5)
        result[f"{name}_sigma"] = round(float(sigma[i]), 6)
    result["volume"] = round(float(calc_volume(refined, crystal_system)), 4)
    result["n_peaks_found"] = int(len(peaks))
    result["n_peaks_matched"] = int(len(matches))
    result["residual"] = round(cost, 8)
    return result


# ─── Multi-temperature analysis ──────────────────────────────────────
def extract_temperature(filename, pattern=None):
    """Try to extract temperature (K) from filename."""
    if pattern:
        m = re.search(pattern, filename)
        if m:
            return float(m.group(1))
    # Auto: look for integers 100..2000
    for m in re.finditer(r"(\d+)", filename):
        t = int(m.group(1))
        if 100 <= t <= 2000:
            return float(t)
    return None


def r_squared(x, y, coeffs):
    """R² for a polynomial fit."""
    pred = np.polyval(coeffs, x)
    ss_res = np.sum((y - pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def analyze_thermal_expansion(results, crystal_system):
    """Detect phase transition and fit thermal expansion per phase."""
    good = [r for r in results
            if r.get("success") and r.get("temperature_K") is not None]
    good.sort(key=lambda r: r["temperature_K"])
    if len(good) < 3:
        return None

    temps = np.array([r["temperature_K"] for r in good])
    vols = np.array([r["volume"] for r in good])

    # Single-phase linear fit of V vs T
    c1_single = np.polyfit(temps, vols, 1)
    r2_single = r_squared(temps, vols, c1_single)

    # Try every possible split into two phases (at least 2 points each)
    best_split = None
    best_total_res = float("inf")
    for si in range(2, len(temps) - 1):
        t1, v1 = temps[:si], vols[:si]
        t2, v2 = temps[si:], vols[si:]
        c1 = np.polyfit(t1, v1, 1)
        c2 = np.polyfit(t2, v2, 1)
        res = np.sum((np.polyval(c1, t1) - v1) ** 2) + np.sum(
            (np.polyval(c2, t2) - v2) ** 2
        )
        if res < best_total_res:
            best_total_res = res
            best_split = si

    single_res = np.sum((np.polyval(c1_single, temps) - vols) ** 2)

    thermal = {"temperatures_K": temps.tolist(), "volumes": vols.tolist()}

    param_names = PARAM_NAMES[crystal_system]

    def _fit_phase(idx_slice, label):
        """Build per-phase fit dict for V and each lattice parameter."""
        t = temps[idx_slice]
        v = vols[idx_slice]
        c = np.polyfit(t, v, 1)
        phase = {
            "temperature_range_K": [float(t[0]), float(t[-1])],
            "V_slope": round(float(c[0]), 6),
            "V_intercept": round(float(c[1]), 4),
            "V_R_squared": round(r_squared(t, v, c), 6),
        }
        # Fit each lattice parameter
        for pn in param_names:
            vals = np.array([good[i][pn] for i in range(len(good))])[idx_slice]
            cp = np.polyfit(t, vals, 1)
            phase[f"{pn}_slope"] = round(float(cp[0]), 8)
            phase[f"{pn}_intercept"] = round(float(cp[1]), 5)
            phase[f"{pn}_R_squared"] = round(r_squared(t, vals, cp), 6)
        return phase

    # Decide: two-phase if residual drops by >60%
    if best_split and best_total_res < single_res * 0.4:
        thermal["phase_transition"] = True
        thermal["transition_temperature_K"] = round(
            float((temps[best_split - 1] + temps[best_split]) / 2), 1
        )
        thermal["phase_1"] = _fit_phase(slice(0, best_split), "RTP")
        thermal["phase_2"] = _fit_phase(slice(best_split, None), "HTP")
    else:
        thermal["phase_transition"] = False
        thermal["single_phase"] = _fit_phase(slice(None), "single")

    return thermal


# ─── Main ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Pawley-type PXRD lattice parameter refinement"
    )
    ap.add_argument("--file", help="Single PXRD data file")
    ap.add_argument("--dir", help="Directory with multiple PXRD files")
    ap.add_argument(
        "--crystal-system", required=True,
        choices=list(PARAM_NAMES.keys()),
    )
    ap.add_argument(
        "--initial-params", required=True,
        help='Initial lattice params, e.g. "a=10.8,c=6.5"',
    )
    ap.add_argument("--wavelength", type=float, default=1.5406,
                    help="X-ray wavelength in Å (default: Cu Kα1 = 1.5406)")
    ap.add_argument("--max-index", type=int, default=10,
                    help="Max Miller index for peak generation")
    ap.add_argument("--tolerance", type=float, default=0.3,
                    help="Peak matching tolerance in 2θ degrees")
    ap.add_argument("--multi-temp", action="store_true",
                    help="Process all files in --dir for thermal expansion")
    ap.add_argument("--temp-pattern", default=None,
                    help='Regex to extract T from filename, e.g. "(\\d+)K"')
    ap.add_argument("-o", "--output", help="Save JSON to file")
    args = ap.parse_args()

    # Parse initial parameters
    parts = {}
    for item in args.initial_params.split(","):
        k, v = item.strip().split("=")
        parts[k.strip().lower()] = float(v.strip())
    names = PARAM_NAMES[args.crystal_system]
    initial_params = [parts.get(n, 90.0 if n in ("alpha", "beta", "gamma") else 5.0)
                      for n in names]

    if args.file:
        output = process_single(
            args.file, args.crystal_system, initial_params,
            args.wavelength, args.max_index, args.tolerance,
        )
    elif args.dir:
        data_dir = Path(args.dir)
        exts = ("*.xy", "*.dat", "*.csv", "*.txt", "*.raw")
        files = []
        for ext in exts:
            files.extend(data_dir.glob(ext))
        files = sorted(set(files))
        if not files:
            print(json.dumps({"success": False, "error": "No data files found"}))
            sys.exit(1)

        results = []
        current_params = list(initial_params)
        for f in files:
            temp = extract_temperature(f.stem, args.temp_pattern)
            r = process_single(
                str(f), args.crystal_system, current_params,
                args.wavelength, args.max_index, args.tolerance,
            )
            r["temperature_K"] = temp
            results.append(r)
            # Use refined as next starting point
            if r.get("success"):
                current_params = [r[n] for n in names]

        if args.multi_temp:
            thermal = analyze_thermal_expansion(results, args.crystal_system)
            output = {
                "success": True,
                "per_temperature": results,
                "thermal_expansion": thermal,
            }
        else:
            output = {"success": True, "per_temperature": results}
    else:
        ap.error("Provide --file or --dir")
        return

    json_str = json.dumps(output, indent=2, ensure_ascii=False)
    print(json_str)
    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        print(f"\nSaved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
