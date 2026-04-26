#!/usr/bin/env python3
"""
solve_refine_scxrd.py — Single-crystal XRD structure solution & refinement.

Pipeline:
  1. Parse HKL (SHELX HKLF4) + P4P/INS files
  2. Try SHELX (shelxl) if installed; otherwise Python charge-flipping fallback
  3. Least-squares refinement of positions + isotropic displacement on F²
  4. Write IUCr-compliant CIF with R-factors, GOOF, symmetry ops, etc.

Usage:
  python solve_refine_scxrd.py --hkl data.hkl [--ins data.ins] [--p4p data.p4p] \\
      [--sg "P21"] [--elements Fe C H N O] [--grid 72] [--trials 2] \\
      [--cycles 400] [--output result.cif]
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

# Import companion library
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from solve_refine_scxrd_lib import (
    CROMER_MANN,
    charge_flipping,
    crystal_system,
    find_atoms_in_density,
    get_sg_number,
    get_sg_ops,
    molecular_weight,
    scattering_factor,
    symop_to_xyz,
)


# ───────────────────── File Parsers ─────────────────────

def parse_hkl(path: str) -> np.ndarray:
    """Parse SHELX HKLF4 format. Returns Nx5 array: h,k,l,F²,σ(F²)."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            # HKLF4 fixed format: h(4) k(4) l(4) F2(8) sig(8)
            # But also handle free-format
            if len(line) >= 28 and line[:4].strip().lstrip('-').isdigit():
                try:
                    h = int(line[0:4])
                    k = int(line[4:8])
                    l = int(line[8:12])
                    f2 = float(line[12:20])
                    sig = float(line[20:28])
                    if h == 0 and k == 0 and l == 0:
                        break  # end of data marker
                    rows.append([h, k, l, f2, sig])
                    continue
                except (ValueError, IndexError):
                    pass
            # Try free format
            parts = line.split()
            if len(parts) >= 5:
                try:
                    h, k, l = int(parts[0]), int(parts[1]), int(parts[2])
                    f2, sig = float(parts[3]), float(parts[4])
                    if h == 0 and k == 0 and l == 0:
                        break
                    rows.append([h, k, l, f2, sig])
                except ValueError:
                    continue
    if not rows:
        raise ValueError(f"No reflections parsed from {path}")
    return np.array(rows, dtype=float)


def parse_ins(path: str) -> dict:
    """Parse SHELX INS/RES file for cell, atoms, SG, elements."""
    result = {
        "cell": None, "wavelength": None, "sg": None, "sg_name": None,
        "elements": [], "unit": [], "atoms": [], "symm_ops_str": [],
        "latt": None,
    }
    with open(path) as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        parts = line.split()
        if not parts:
            i += 1
            continue
        cmd = parts[0].upper()
        if cmd == "TITL" and len(parts) >= 3:
            # Try to extract SG from title "NAME in SG"
            if "in" in parts:
                idx = parts.index("in")
                if idx + 1 < len(parts):
                    result["sg_name"] = " ".join(parts[idx+1:])
        elif cmd == "CELL" and len(parts) >= 8:
            result["wavelength"] = float(parts[1])
            result["cell"] = tuple(float(x) for x in parts[2:8])
        elif cmd == "LATT":
            result["latt"] = int(parts[1])
        elif cmd == "SYMM":
            result["symm_ops_str"].append(" ".join(parts[1:]))
        elif cmd == "SFAC":
            result["elements"] = [p for p in parts[1:] if p.isalpha()]
        elif cmd == "UNIT" and len(parts) > 1:
            result["unit"] = [int(float(x)) for x in parts[1:]]
        elif cmd in ("HKLF", "END"):
            pass
        elif cmd not in ("ZERR", "TEMP", "L.S.", "PLAN", "BOND", "FMAP",
                         "ACTA", "WGHT", "FVAR", "REM", "SIZE", "SHEL",
                         "OMIT", "EXTI", "BASF", "TWIN", "ANIS", "DFIX",
                         "SADI", "FLAT", "EADP", "ISOR", "SIMU", "DELU",
                         "BUMP", "SWAT", "MERG", "SPEC", "MORE", "CONN",
                         "CONF", "MPLA", "HTAB", "LIST", "BLOC", "WPDB") \
             and len(parts) >= 6 and not cmd.startswith("REM"):
            # Potential atom line: Label Type x y z occ Uiso
            try:
                elem_idx = int(parts[1])
                x = float(parts[2])
                y = float(parts[3])
                z = float(parts[4])
                occ_code = float(parts[5])
                occ = occ_code  # often 11.00000 means part 1, occ=1
                if occ > 10:
                    occ = 1.0
                uiso = float(parts[6]) if len(parts) > 6 else 0.05
                if elem_idx < 1 or elem_idx > len(result["elements"]):
                    i += 1
                    continue
                elem = result["elements"][elem_idx - 1]
                label = parts[0]
                # Wrap fractional coords to [0, 1)
                x = x % 1.0
                y = y % 1.0
                z = z % 1.0
                result["atoms"].append({
                    "label": label, "element": elem,
                    "x": x, "y": y, "z": z,
                    "occ": min(occ, 1.0), "uiso": abs(uiso),
                })
            except (ValueError, IndexError):
                pass
        i += 1

    # Determine SG from LATT + SYMM
    if result["sg_name"]:
        result["sg"] = result["sg_name"]
    elif result["symm_ops_str"]:
        # Try to identify from SYMM cards
        symm_str = "; ".join(result["symm_ops_str"]).upper()
        if "-X,0.5+Y,-Z" in symm_str.replace(" ", "") or \
           "-X,Y+1/2,-Z" in symm_str.replace(" ", "").replace("0.5", "1/2"):
            result["sg"] = "P21"
        elif "1/2-X" in symm_str or "-X+1/2" in symm_str:
            result["sg"] = "P212121"
        else:
            result["sg"] = "P1"
    return result


def parse_p4p(path: str) -> dict:
    """Parse Bruker P4P file for cell parameters, wavelength, composition."""
    result = {"cell": None, "wavelength": None, "composition": None}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "CELL" and len(parts) >= 8:
                result["cell"] = tuple(float(x) for x in parts[1:7])
                # Volume is often parts[7]
            elif parts[0] == "SOURCE" and len(parts) >= 2:
                result["wavelength"] = float(parts[2]) if len(parts) >= 3 else 0.71073
            elif parts[0] == "CHEM":
                result["composition"] = parts[1] if len(parts) >= 2 else None
    return result


# ───────────────────── Structure Factor Calculation ─────────────────────

def calc_cell_volume(cell: Tuple[float, ...]) -> float:
    """Calculate unit cell volume from a,b,c,alpha,beta,gamma."""
    a, b, c, al, be, ga = cell
    al, be, ga = math.radians(al), math.radians(be), math.radians(ga)
    v = a * b * c * math.sqrt(
        1 - math.cos(al)**2 - math.cos(be)**2 - math.cos(ga)**2
        + 2 * math.cos(al) * math.cos(be) * math.cos(ga)
    )
    return v


def calc_metric_tensor(cell: Tuple[float, ...]) -> np.ndarray:
    """Calculate reciprocal metric tensor for d-spacing calculations."""
    a, b, c, al, be, ga = cell
    al, be, ga = math.radians(al), math.radians(be), math.radians(ga)
    V = calc_cell_volume(cell)
    astar = b * c * math.sin(al) / V
    bstar = a * c * math.sin(be) / V
    cstar = a * b * math.sin(ga) / V
    cos_alpha_star = (math.cos(be) * math.cos(ga) - math.cos(al)) / (math.sin(be) * math.sin(ga))
    cos_beta_star = (math.cos(al) * math.cos(ga) - math.cos(be)) / (math.sin(al) * math.sin(ga))
    cos_gamma_star = (math.cos(al) * math.cos(be) - math.cos(ga)) / (math.sin(al) * math.sin(be))
    G = np.array([
        [astar**2, astar*bstar*cos_gamma_star, astar*cstar*cos_beta_star],
        [astar*bstar*cos_gamma_star, bstar**2, bstar*cstar*cos_alpha_star],
        [astar*cstar*cos_beta_star, bstar*cstar*cos_alpha_star, cstar**2],
    ])
    return G


def calc_sin_theta_over_lambda(hkl: np.ndarray, cell: Tuple[float, ...], wavelength: float) -> np.ndarray:
    """Calculate sin(θ)/λ for each reflection."""
    G = calc_metric_tensor(cell)
    h, k, l = hkl[:, 0], hkl[:, 1], hkl[:, 2]
    d_star_sq = (G[0,0]*h*h + G[1,1]*k*k + G[2,2]*l*l +
                 2*G[0,1]*h*k + 2*G[0,2]*h*l + 2*G[1,2]*k*l)
    d_star_sq = np.maximum(d_star_sq, 1e-10)
    return np.sqrt(d_star_sq) / 2.0


def calc_structure_factors(
    atoms: List[dict],
    hkl: np.ndarray,
    cell: Tuple[float, ...],
    wavelength: float,
    sg_ops: List[Tuple[np.ndarray, np.ndarray]],
) -> np.ndarray:
    """Calculate |F_calc|² for each reflection."""
    h, k, l = hkl[:, 0], hkl[:, 1], hkl[:, 2]
    stol = calc_sin_theta_over_lambda(hkl, cell, wavelength)

    F_calc = np.zeros(len(h), dtype=complex)

    for atom in atoms:
        elem = atom["element"]
        f0 = scattering_factor(elem, stol)
        B = atom["uiso"] * 8 * math.pi**2  # Uiso to B-factor
        dwf = np.exp(-B * stol**2)  # Debye-Waller factor

        for rot, trans in sg_ops:
            # Apply symmetry: x' = R·x + t
            xyz = np.array([atom["x"], atom["y"], atom["z"]])
            xyz_sym = rot @ xyz + trans

            phase = 2 * math.pi * (h * xyz_sym[0] + k * xyz_sym[1] + l * xyz_sym[2])
            F_calc += atom["occ"] * f0 * dwf * np.exp(1j * phase)

    return np.abs(F_calc) ** 2


def refine_structure(
    atoms: List[dict],
    hkl_data: np.ndarray,
    cell: Tuple[float, ...],
    wavelength: float,
    sg_ops: List[Tuple[np.ndarray, np.ndarray]],
    max_iter: int = 15,
) -> Tuple[List[dict], dict]:
    """
    Least-squares refinement of atom positions and Uiso against F².
    Returns (refined_atoms, stats_dict).
    """
    h_arr = hkl_data[:, 0:3]
    F2_obs = hkl_data[:, 3]
    sig_F2 = hkl_data[:, 4]

    # Filter out weak/negative reflections for stability
    mask = (F2_obs > 0) & (sig_F2 > 0)
    h_arr = h_arr[mask]
    F2_obs = F2_obs[mask]
    sig_F2 = sig_F2[mask]

    # Weights
    w = 1.0 / (sig_F2**2 + (0.05 * F2_obs)**2)  # SHELX-like weighting
    w_sqrt = np.sqrt(w)

    stol = calc_sin_theta_over_lambda(h_arr, cell, wavelength)
    h, k, l = h_arr[:, 0], h_arr[:, 1], h_arr[:, 2]

    n_atoms = len(atoms)
    # Pack parameters: [x0,y0,z0,uiso0, x1,y1,z1,uiso1, ..., scale]
    x0 = []
    for atom in atoms:
        x0.extend([atom["x"], atom["y"], atom["z"], max(atom["uiso"], 0.005)])
    x0.append(1.0)  # scale factor
    x0 = np.array(x0, dtype=float)

    def residuals(params):
        scale = params[-1]
        F_calc = np.zeros(len(h), dtype=complex)
        for ia in range(n_atoms):
            base = ia * 4
            ax, ay, az, au = params[base:base+4]
            elem = atoms[ia]["element"]
            occ = atoms[ia]["occ"]
            f0 = scattering_factor(elem, stol)
            B = au * 8 * math.pi**2
            dwf = np.exp(-B * stol**2)
            for rot, trans in sg_ops:
                xyz = np.array([ax, ay, az])
                xyz_sym = rot @ xyz + trans
                phase = 2 * math.pi * (h * xyz_sym[0] + k * xyz_sym[1] + l * xyz_sym[2])
                F_calc += occ * f0 * dwf * np.exp(1j * phase)
        F2_calc = np.abs(F_calc) ** 2
        return w_sqrt * (F2_obs - scale * F2_calc)

    print(f"  Refining {n_atoms} atoms, {len(F2_obs)} reflections...")
    t0 = time.time()

    try:
        result = least_squares(
            residuals, x0,
            method='trf',
            max_nfev=max_iter * len(x0),
            ftol=1e-4, xtol=1e-4, gtol=1e-4,
            verbose=0,
        )
        params = result.x
        print(f"  Refinement completed in {time.time()-t0:.1f}s, cost={result.cost:.2f}")
    except Exception as e:
        print(f"  Refinement failed: {e}, using initial parameters")
        params = x0

    # Extract refined atoms
    scale = params[-1]
    refined_atoms = []
    for ia in range(n_atoms):
        base = ia * 4
        refined_atoms.append({
            "label": atoms[ia]["label"],
            "element": atoms[ia]["element"],
            "x": params[base] % 1.0,
            "y": params[base+1] % 1.0,
            "z": params[base+2] % 1.0,
            "occ": atoms[ia]["occ"],
            "uiso": max(params[base+3], 0.001),
        })

    # Calculate R-factors
    F_calc_final = np.zeros(len(h), dtype=complex)
    for ia in range(n_atoms):
        base = ia * 4
        ax, ay, az, au = params[base:base+4]
        elem = atoms[ia]["element"]
        occ = atoms[ia]["occ"]
        f0 = scattering_factor(elem, stol)
        B = au * 8 * math.pi**2
        dwf = np.exp(-B * stol**2)
        for rot, trans in sg_ops:
            xyz = np.array([ax, ay, az])
            xyz_sym = rot @ xyz + trans
            phase = 2 * math.pi * (h * xyz_sym[0] + k * xyz_sym[1] + l * xyz_sym[2])
            F_calc_final += occ * f0 * dwf * np.exp(1j * phase)

    F2_calc = scale * np.abs(F_calc_final) ** 2

    # R1 on F (for F² > 2σ)
    strong = F2_obs > 2 * sig_F2
    F_obs_strong = np.sqrt(np.maximum(F2_obs[strong], 0))
    F_calc_strong = np.sqrt(np.maximum(F2_calc[strong], 0))
    R1 = np.sum(np.abs(F_obs_strong - F_calc_strong)) / np.sum(F_obs_strong) if np.sum(F_obs_strong) > 0 else 999

    # R1_all
    F_obs_all = np.sqrt(np.maximum(F2_obs, 0))
    F_calc_all = np.sqrt(np.maximum(F2_calc, 0))
    R1_all = np.sum(np.abs(F_obs_all - F_calc_all)) / np.sum(F_obs_all) if np.sum(F_obs_all) > 0 else 999

    # wR2
    num = np.sum(w * (F2_obs - F2_calc)**2)
    den = np.sum(w * F2_obs**2)
    wR2 = math.sqrt(num / den) if den > 0 else 999

    # GooF
    n_params = n_atoms * 4 + 1
    n_refl = len(F2_obs)
    goof = math.sqrt(num / max(n_refl - n_params, 1))

    stats = {
        "R1": R1,
        "R1_all": R1_all,
        "wR2": wR2,
        "GooF": goof,
        "n_reflections": n_refl,
        "n_parameters": n_params,
        "scale": scale,
    }
    print(f"  R1 = {R1:.4f}, wR2 = {wR2:.4f}, GooF = {goof:.3f}")
    return refined_atoms, stats


# ───────────────────── CIF Writer ─────────────────────

def _hill_formula(elements: List[str], counts: Dict[str, int]) -> str:
    """Format chemical formula in Hill order."""
    ordered = []
    # Hill order: C first, then H, then alphabetical
    if "C" in counts and counts["C"] > 0:
        ordered.append("C")
        if counts["C"] > 1:
            ordered[-1] += str(counts["C"])
        if "H" in counts and counts["H"] > 0:
            ordered.append("H")
            if counts["H"] > 1:
                ordered[-1] += str(counts["H"])
    remaining = sorted([e for e in counts if e not in ("C", "H") and counts[e] > 0])
    for elem in remaining:
        s = elem
        if counts[elem] > 1:
            s += str(counts[elem])
        ordered.append(s)
    return " ".join(ordered)


def write_cif(
    output_path: str,
    atoms: List[dict],
    cell: Tuple[float, ...],
    wavelength: float,
    sg_name: str,
    sg_ops: List[Tuple[np.ndarray, np.ndarray]],
    stats: dict,
    elements: List[str],
    unit_counts: List[int],
    data_name: str = "structure",
):
    """Write IUCr-compliant CIF file."""
    a, b, c, alpha, beta, gamma = cell
    V = calc_cell_volume(cell)
    sg_num = get_sg_number(sg_name)
    cs = crystal_system(sg_num)
    Z = len(sg_ops)

    # Formula from asymmetric unit atoms
    elem_counts: Dict[str, int] = {}
    for atom in atoms:
        elem_counts[atom["element"]] = elem_counts.get(atom["element"], 0) + 1
    formula = _hill_formula(elements, elem_counts)

    # Molecular weight
    mw = molecular_weight(list(elem_counts.keys()), list(elem_counts.values()))

    # Density
    NA = 6.02214076e23
    density = (Z * mw) / (V * 1e-24 * NA) if V > 0 else 0

    # Radiation type
    if abs(wavelength - 0.71073) < 0.01:
        rad_type = "Mo K\\a"
    elif abs(wavelength - 1.5406) < 0.01 or abs(wavelength - 1.54184) < 0.01:
        rad_type = "Cu K\\a"
    elif abs(wavelength - 0.5608) < 0.01:
        rad_type = "Ag K\\a"
    else:
        rad_type = f"synchrotron, {wavelength:.5f}"

    with open(output_path, 'w') as f:
        f.write(f"data_{data_name}\n")
        f.write("_audit_creation_method            'solve_refine_scxrd.py'\n")
        f.write(f"\n")

        # Cell
        f.write(f"_cell_length_a                    {a:.5f}\n")
        f.write(f"_cell_length_b                    {b:.5f}\n")
        f.write(f"_cell_length_c                    {c:.5f}\n")
        f.write(f"_cell_angle_alpha                 {alpha:.3f}\n")
        f.write(f"_cell_angle_beta                  {beta:.3f}\n")
        f.write(f"_cell_angle_gamma                 {gamma:.3f}\n")
        f.write(f"_cell_volume                      {V:.2f}\n")
        f.write(f"_cell_formula_units_Z             {Z}\n")
        f.write(f"\n")

        # Symmetry
        f.write(f"_space_group_crystal_system       {cs}\n")
        f.write(f"_space_group_IT_number            {sg_num}\n")
        f.write(f"_space_group_name_H-M_alt         '{sg_name}'\n")
        f.write(f"_symmetry_space_group_name_H-M    '{sg_name}'\n")
        f.write(f"_symmetry_Int_Tables_number       {sg_num}\n")
        f.write(f"_symmetry_cell_setting            {cs}\n")
        f.write(f"\n")

        # Symmetry operations
        f.write("loop_\n")
        f.write("_symmetry_equiv_pos_site_id\n")
        f.write("_symmetry_equiv_pos_as_xyz\n")
        for idx, (rot, trans) in enumerate(sg_ops, 1):
            xyz_str = symop_to_xyz(rot, trans)
            f.write(f"{idx} '{xyz_str}'\n")
        f.write(f"\n")

        # Chemistry
        f.write(f"_chemical_formula_sum              '{formula}'\n")
        f.write(f"_chemical_formula_moiety           '{formula}'\n")
        f.write(f"_chemical_formula_weight           {mw:.2f}\n")
        f.write(f"\n")

        # Experiment
        f.write(f"_exptl_crystal_density_diffrn     {density:.3f}\n")
        f.write(f"_diffrn_radiation_wavelength      {wavelength:.5f}\n")
        f.write(f"_diffrn_radiation_type            '{rad_type}'\n")
        f.write(f"\n")

        # Refinement
        f.write(f"_refine_ls_R_factor_gt            {stats['R1']:.4f}\n")
        f.write(f"_refine_ls_R_factor_all           {stats['R1_all']:.4f}\n")
        f.write(f"_refine_ls_wR_factor_ref          {stats['wR2']:.4f}\n")
        f.write(f"_refine_ls_goodness_of_fit_ref    {stats['GooF']:.3f}\n")
        f.write(f"_refine_ls_number_reflns          {stats['n_reflections']}\n")
        f.write(f"_refine_ls_number_parameters      {stats['n_parameters']}\n")
        f.write("_refine_ls_weighting_scheme       calc\n")
        f.write("_refine_ls_weighting_details      'w=1/[s^2(Fo^2)+(0.0500P)^2] where P=(Fo^2+2Fc^2)/3'\n")
        f.write(f"\n")

        # Atoms
        f.write("loop_\n")
        f.write("_atom_site_label\n")
        f.write("_atom_site_type_symbol\n")
        f.write("_atom_site_fract_x\n")
        f.write("_atom_site_fract_y\n")
        f.write("_atom_site_fract_z\n")
        f.write("_atom_site_U_iso_or_equiv\n")
        f.write("_atom_site_adp_type\n")
        f.write("_atom_site_occupancy\n")
        for atom in atoms:
            f.write(f"{atom['label']:<7s}{atom['element']:<7s}"
                    f"{atom['x']:9.5f} {atom['y']:9.5f} {atom['z']:9.5f} "
                    f"{atom['uiso']:.4f} Uiso {atom['occ']:.4f}\n")
        f.write(f"\n")

    print(f"  CIF written to {output_path}")


# ───────────────────── SHELX Integration ─────────────────────

def try_shelx(ins_path: str, hkl_path: str) -> Optional[str]:
    """Try to run SHELXL if available. Returns path to .res file or None."""
    shelxl = shutil.which("shelxl") or shutil.which("shelxl-2019") or shutil.which("shelxl-2018")
    if shelxl is None:
        return None

    basename = Path(ins_path).stem
    work_dir = Path(ins_path).parent
    try:
        result = subprocess.run(
            [shelxl, basename],
            cwd=str(work_dir),
            capture_output=True, text=True, timeout=300,
        )
        res_path = work_dir / f"{basename}.res"
        if res_path.exists():
            return str(res_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ───────────────────── Main Pipeline ─────────────────────

def main():
    parser = argparse.ArgumentParser(description="SCXRD structure solution & refinement")
    parser.add_argument("--hkl", required=True, help="HKL file (SHELX HKLF4)")
    parser.add_argument("--ins", help="INS file (SHELX instruction)")
    parser.add_argument("--p4p", help="P4P file (Bruker)")
    parser.add_argument("--sg", help="Space group (e.g. 'P21', 'P21/c', '14')")
    parser.add_argument("--elements", nargs="+", help="Elements present (e.g. Fe C H N O)")
    parser.add_argument("--grid", type=int, default=72, help="Charge-flipping grid size")
    parser.add_argument("--trials", type=int, default=2, help="Number of CF trials")
    parser.add_argument("--cycles", type=int, default=400, help="Max CF cycles per trial")
    parser.add_argument("--max-iter", type=int, default=15, help="LS refinement max iterations")
    parser.add_argument("--output", "-o", default="result.cif", help="Output CIF path")
    parser.add_argument("--json", help="Output JSON summary path")
    args = parser.parse_args()

    print("=" * 60)
    print("SCXRD Structure Solution & Refinement Pipeline")
    print("=" * 60)

    # ─── Parse input files ───
    print("\n[1/5] Parsing input files...")
    hkl_data = parse_hkl(args.hkl)
    print(f"  HKL: {len(hkl_data)} reflections from {args.hkl}")

    ins_data = None
    if args.ins and os.path.exists(args.ins):
        ins_data = parse_ins(args.ins)
        print(f"  INS: {len(ins_data.get('atoms', []))} atoms, SG={ins_data.get('sg')}")
    elif not args.ins:
        # Try to find INS with same stem as HKL
        stem = Path(args.hkl).stem
        for ext in (".ins", ".res"):
            candidate = Path(args.hkl).parent / (stem + ext)
            if candidate.exists():
                ins_data = parse_ins(str(candidate))
                print(f"  INS: auto-found {candidate}, {len(ins_data.get('atoms', []))} atoms")
                args.ins = str(candidate)
                break

    p4p_data = None
    if args.p4p and os.path.exists(args.p4p):
        p4p_data = parse_p4p(args.p4p)
        print(f"  P4P: cell={p4p_data.get('cell')}, λ={p4p_data.get('wavelength')}")
    elif not args.p4p:
        stem = Path(args.hkl).stem
        candidate = Path(args.hkl).parent / (stem + ".p4p")
        if candidate.exists():
            p4p_data = parse_p4p(str(candidate))
            print(f"  P4P: auto-found {candidate}")

    # ─── Determine cell, SG, wavelength ───
    cell = None
    wavelength = 0.71073  # Default Mo Kα
    sg_name = args.sg

    if ins_data and ins_data["cell"]:
        cell = ins_data["cell"]
        if ins_data["wavelength"]:
            wavelength = ins_data["wavelength"]
        if not sg_name and ins_data["sg"]:
            sg_name = ins_data["sg"]

    if p4p_data:
        if not cell and p4p_data["cell"]:
            cell = p4p_data["cell"]
        if p4p_data["wavelength"]:
            wavelength = p4p_data["wavelength"]

    if cell is None:
        print("ERROR: No cell parameters found. Provide --ins or --p4p file.")
        sys.exit(1)

    if sg_name is None:
        sg_name = "P1"
        print("  WARNING: No space group specified, using P1")

    sg_ops = get_sg_ops(sg_name)
    print(f"\n  Cell: a={cell[0]:.4f} b={cell[1]:.4f} c={cell[2]:.4f}")
    print(f"        α={cell[3]:.3f} β={cell[4]:.3f} γ={cell[5]:.3f}")
    print(f"  Volume: {calc_cell_volume(cell):.2f} Å³")
    print(f"  Space group: {sg_name} ({len(sg_ops)} ops)")
    print(f"  Wavelength: {wavelength:.5f} Å")

    # Elements
    elements = args.elements
    if not elements and ins_data and ins_data["elements"]:
        elements = ins_data["elements"]
    if not elements:
        elements = ["C", "H", "N", "O"]
        print("  WARNING: No elements specified, assuming C H N O")

    unit_counts = []
    if ins_data and ins_data["unit"]:
        unit_counts = ins_data["unit"]

    # ─── Try SHELX first ───
    print("\n[2/5] Attempting SHELX refinement...")
    atoms = None
    if args.ins and os.path.exists(args.ins):
        res_path = try_shelx(args.ins, args.hkl)
        if res_path:
            print(f"  SHELX succeeded → {res_path}")
            res_data = parse_ins(res_path)
            if res_data["atoms"]:
                atoms = res_data["atoms"]
                print(f"  Using {len(atoms)} atoms from SHELX result")

    # ─── Use INS atoms if available ───
    if atoms is None and ins_data and ins_data["atoms"]:
        print("  SHELX not available/failed, using atoms from INS file")
        atoms = ins_data["atoms"]
        print(f"  {len(atoms)} atoms from INS")

    # ─── Charge-flipping fallback ───
    if atoms is None or len(atoms) == 0:
        print("\n[3/5] Charge-flipping structure solution...")
        best_density = None
        best_score = -1
        for trial in range(args.trials):
            print(f"  Trial {trial+1}/{args.trials} (grid={args.grid}, cycles={args.cycles})")
            rho = charge_flipping(
                hkl_data, cell, sg_ops,
                grid=args.grid, cycles=args.cycles,
                sigma_thresh=3.5,
                seed=42 + trial * 17,
            )
            if rho is not None:
                score = np.max(rho) / np.std(rho)
                print(f"    Peak/sigma = {score:.1f}")
                if score > best_score:
                    best_score = score
                    best_density = rho

        if best_density is not None:
            peaks = find_atoms_in_density(best_density, cell, sigma_thresh=3.5)
            print(f"  Found {len(peaks)} peaks in density map")

            # Assign elements to peaks by height (heaviest first)
            elem_sorted = sorted(
                [(e, CROMER_MANN.get(e, (0,))[0]) for e in elements if e != "H"],
                key=lambda x: -x[1]
            )
            atoms = []
            for i, (x, y, z, height) in enumerate(peaks[:50]):  # limit
                # Assign element by peak height
                if i == 0 and elem_sorted:
                    elem = elem_sorted[0][0]
                else:
                    idx = min(i, len(elem_sorted) - 1)
                    elem = elem_sorted[idx][0] if elem_sorted else "C"
                atoms.append({
                    "label": f"{elem}{i+1}",
                    "element": elem,
                    "x": x, "y": y, "z": z,
                    "occ": 1.0, "uiso": 0.05,
                })
            print(f"  Assigned {len(atoms)} atoms")
        else:
            print("  ERROR: Charge-flipping failed to produce density")
            atoms = []
    else:
        print("\n[3/5] Skipping charge-flipping (have initial model)")

    if not atoms:
        print("ERROR: No atoms to refine. Cannot proceed.")
        sys.exit(1)

    # ─── Least-squares refinement ───
    print(f"\n[4/5] Least-squares refinement...")
    refined_atoms, stats = refine_structure(
        atoms, hkl_data, cell, wavelength, sg_ops,
        max_iter=args.max_iter,
    )

    # ─── Write CIF ───
    print(f"\n[5/5] Writing CIF...")
    data_name = Path(args.hkl).stem
    write_cif(
        args.output, refined_atoms, cell, wavelength,
        sg_name, sg_ops, stats, elements, unit_counts,
        data_name=data_name,
    )

    # ─── Summary ───
    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)
    print(f"  R1 (F > 2σ)  = {stats['R1']:.4f}")
    print(f"  R1 (all)     = {stats['R1_all']:.4f}")
    print(f"  wR2          = {stats['wR2']:.4f}")
    print(f"  GooF         = {stats['GooF']:.3f}")
    print(f"  Reflections  = {stats['n_reflections']}")
    print(f"  Parameters   = {stats['n_parameters']}")
    print(f"  CIF          = {args.output}")

    # Optional JSON output
    if args.json:
        summary = {
            "R1": stats["R1"],
            "R1_all": stats["R1_all"],
            "wR2": stats["wR2"],
            "GooF": stats["GooF"],
            "n_reflections": stats["n_reflections"],
            "n_parameters": stats["n_parameters"],
            "cell": list(cell),
            "sg": sg_name,
            "n_atoms": len(refined_atoms),
            "cif_path": args.output,
        }
        with open(args.json, 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"  JSON summary = {args.json}")


if __name__ == "__main__":
    main()
