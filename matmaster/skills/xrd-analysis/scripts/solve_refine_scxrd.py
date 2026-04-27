#!/usr/bin/env python3
"""
solve_refine_scxrd.py — Single-crystal XRD structure solution & refinement pipeline.

Usage:
  python3 solve_refine_scxrd.py --hkl data.hkl --sg "P2_1/c" --elements C H N O \\
      --grid 72 --trials 2 --cycles 400 --output result.cif --json result.json

Workflow:
  1. Parse HKL (SHELX HKLF4), INS, P4P files
  2. Auto-discover companion files (INS/P4P from HKL stem)
  3. Try SHELX (shelxs/shelxl) if installed; fall back to Python charge-flipping
  4. Least-squares refinement of positions + Uiso against F² (scipy)
  5. Write IUCr-compliant CIF output

Requires: numpy, scipy
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import numpy as np

# Import the helper library (same directory)
sys.path.insert(0, str(Path(__file__).parent))
from solve_refine_scxrd_lib import (
    CROMER_MANN, ATOMIC_WEIGHTS, SPACE_GROUPS, SG_ALIASES,
    lookup_sg, get_crystal_system, calc_f_calc,
    charge_flipping, find_atoms_from_density,
    symop_to_xyz, molecular_weight, cell_volume, hill_formula,
)


# ---------------------------------------------------------------------------
# File parsers
# ---------------------------------------------------------------------------

def parse_hkl(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Parse a SHELX HKLF4 file.
    Returns (hkl, f_obs, sigma) arrays.
    """
    hkl_list, f_list, sig_list = [], [], []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            # HKLF4 fixed-format: h(4) k(4) l(4) F²(8) sigma(8)
            # or free-format
            parts = line.split()
            if len(parts) >= 5:
                h, k, l = int(parts[0]), int(parts[1]), int(parts[2])
                fo2 = float(parts[3])
                sig = float(parts[4])
            elif len(line) >= 28:
                # Fixed-format HKLF4
                try:
                    h = int(line[0:4])
                    k = int(line[4:8])
                    l = int(line[8:12])
                    fo2 = float(line[12:20])
                    sig = float(line[20:28])
                except (ValueError, IndexError):
                    continue
            else:
                continue

            # End marker: 0 0 0
            if h == 0 and k == 0 and l == 0:
                break

            if fo2 > 0 and sig > 0:
                hkl_list.append([h, k, l])
                f_list.append(np.sqrt(fo2))  # |F| from F²
                sig_list.append(sig / (2 * np.sqrt(fo2) + 1e-12))  # sigma(|F|)

    return (np.array(hkl_list, dtype=int),
            np.array(f_list, dtype=float),
            np.array(sig_list, dtype=float))


def parse_ins(path: str) -> Dict:
    """
    Parse a SHELX INS file for cell parameters, space group, wavelength, elements.
    """
    result = {}
    with open(path) as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].upper()

        if cmd == "CELL" and len(parts) >= 8:
            result["wavelength"] = float(parts[1])
            result["cell"] = {
                "a": float(parts[2]), "b": float(parts[3]), "c": float(parts[4]),
                "alpha": float(parts[5]), "beta": float(parts[6]), "gamma": float(parts[7]),
            }
        elif cmd == "LATT":
            result["latt"] = int(parts[1])
        elif cmd == "SYMM":
            result.setdefault("symm_cards", []).append(" ".join(parts[1:]))
        elif cmd == "SFAC":
            result["elements"] = [p for p in parts[1:] if p.isalpha()]
        elif cmd == "ZERR" and len(parts) >= 2:
            result["z"] = int(float(parts[1]))
        elif cmd == "TITL":
            result["title"] = " ".join(parts[1:])

    return result


def parse_p4p(path: str) -> Dict:
    """Parse a Bruker P4P file for cell parameters and space group."""
    result = {}
    with open(path) as f:
        for line in f:
            if line.startswith("CELL"):
                parts = line.split()
                if len(parts) >= 8:
                    result["cell"] = {
                        "a": float(parts[2]), "b": float(parts[3]), "c": float(parts[4]),
                        "alpha": float(parts[5]), "beta": float(parts[6]), "gamma": float(parts[7]),
                    }
                    result["wavelength"] = float(parts[1])
            elif line.startswith("SPGR") or line.startswith("SG "):
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    result["space_group"] = parts[1].strip()
    return result


def auto_discover_files(hkl_path: str) -> Dict[str, str]:
    """
    Given the HKL path, find companion INS and P4P files by stem.
    """
    stem = Path(hkl_path).stem
    directory = Path(hkl_path).parent
    companions = {}
    for ext in [".ins", ".res", ".p4p"]:
        candidate = directory / (stem + ext)
        if candidate.exists():
            companions[ext.lstrip(".")] = str(candidate)
    return companions


# ---------------------------------------------------------------------------
# SHELX fallback
# ---------------------------------------------------------------------------

def try_shelx(hkl_path: str, ins_data: Dict, sg_name: str,
              elements: List[str], cell: Dict, wavelength: float) -> Optional[List[dict]]:
    """
    Attempt structure solution with SHELX (shelxs + shelxl).
    Returns list of atoms or None if SHELX not available.
    """
    shelxs = shutil.which("shelxs")
    shelxl = shutil.which("shelxl")
    if not shelxs or not shelxl:
        return None

    work_dir = Path(hkl_path).parent / "_shelx_work"
    work_dir.mkdir(exist_ok=True)
    name = "struct"

    # Copy HKL
    shutil.copy2(hkl_path, work_dir / f"{name}.hkl")

    # Write INS for shelxs
    sg_info = lookup_sg(sg_name)
    latt = 1  # P
    if sg_name.startswith("C") or sg_name.startswith("A") or sg_name.startswith("I") or sg_name.startswith("F"):
        latt = {"C": 7, "A": 2, "I": 2, "F": 4}.get(sg_name[0], 1)

    z = ins_data.get("z", len(sg_info["ops"]) if sg_info else 2)

    sfac_line = " ".join(elements)
    unit_counts = " ".join([str(z)] * len(elements))

    ins_content = f"""TITL SCXRD solution
CELL {wavelength:.5f} {cell['a']:.4f} {cell['b']:.4f} {cell['c']:.4f} {cell['alpha']:.2f} {cell['beta']:.2f} {cell['gamma']:.2f}
ZERR {z} 0.001 0.001 0.001 0.01 0.01 0.01
LATT {latt}
SFAC {sfac_line}
UNIT {unit_counts}
TREF
HKLF 4
END
"""
    (work_dir / f"{name}.ins").write_text(ins_content)

    try:
        subprocess.run([shelxs, name], cwd=work_dir, capture_output=True, timeout=120)
        res_path = work_dir / f"{name}.res"
        if res_path.exists():
            # Parse atoms from RES
            atoms = _parse_shelx_atoms(res_path, elements)
            if atoms:
                # Run shelxl for refinement
                shutil.copy2(res_path, work_dir / f"{name}.ins")
                subprocess.run([shelxl, name], cwd=work_dir, capture_output=True, timeout=300)
                final_res = work_dir / f"{name}.res"
                if final_res.exists():
                    refined = _parse_shelx_atoms(final_res, elements)
                    if refined:
                        return refined
                return atoms
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def _parse_shelx_atoms(res_path: Path, elements: List[str]) -> List[dict]:
    """Parse atom positions from a SHELX RES file."""
    atoms = []
    with open(res_path) as f:
        in_atoms = False
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "FVAR":
                in_atoms = True
                continue
            if parts[0] in ("HKLF", "END"):
                break
            if in_atoms and len(parts) >= 6:
                try:
                    label = parts[0]
                    sfac_idx = int(parts[1])
                    x = float(parts[2])
                    y = float(parts[3])
                    z = float(parts[4])
                    occ_sof = float(parts[5])
                    uiso = float(parts[6]) if len(parts) > 6 else 0.05

                    elem = elements[sfac_idx - 1] if 1 <= sfac_idx <= len(elements) else "C"
                    occ = abs(occ_sof) if abs(occ_sof) <= 1.0 else 1.0

                    atoms.append({
                        "element": elem, "x": x, "y": y, "z": z,
                        "uiso": abs(uiso), "occ": occ, "label": label,
                    })
                except (ValueError, IndexError):
                    continue
    return atoms


# ---------------------------------------------------------------------------
# Least-squares refinement
# ---------------------------------------------------------------------------

def refine_atoms(hkl: np.ndarray, f_obs: np.ndarray, sigma: np.ndarray,
                 atoms: List[dict], cell: dict, sg_ops: List[tuple],
                 n_cycles: int = 50) -> Tuple[List[dict], dict]:
    """
    Least-squares refinement of positions + Uiso against F².
    Returns (refined_atoms, stats_dict).
    """
    from scipy.optimize import least_squares

    n_atoms = len(atoms)
    # Parameters: [x0, y0, z0, uiso0, x1, y1, z1, uiso1, ..., scale]
    p0 = []
    for at in atoms:
        p0.extend([at["x"], at["y"], at["z"], at.get("uiso", 0.05)])
    p0.append(1.0)  # scale factor
    p0 = np.array(p0, dtype=float)

    fo2 = f_obs ** 2
    w = 1.0 / (sigma ** 2 + 1e-6)

    def residuals(p):
        scale = p[-1]
        atom_list = []
        for i in range(n_atoms):
            idx = i * 4
            atom_list.append({
                "element": atoms[i]["element"],
                "x": p[idx], "y": p[idx + 1], "z": p[idx + 2],
                "uiso": max(p[idx + 3], 0.001),
                "occ": atoms[i].get("occ", 1.0),
            })

        fc = calc_f_calc(hkl, atom_list, cell, sg_ops)
        fc2 = np.abs(fc) ** 2
        return np.sqrt(w) * (fo2 - scale * fc2)

    try:
        result = least_squares(residuals, p0, method="lm",
                               max_nfev=n_cycles * len(p0))

        # Extract refined atoms
        refined = []
        p = result.x
        scale = p[-1]
        for i in range(n_atoms):
            idx = i * 4
            refined.append({
                "element": atoms[i]["element"],
                "x": p[idx] % 1.0,
                "y": p[idx + 1] % 1.0,
                "z": p[idx + 2] % 1.0,
                "uiso": max(abs(p[idx + 3]), 0.001),
                "occ": atoms[i].get("occ", 1.0),
                "label": atoms[i].get("label", f"{atoms[i]['element']}{i+1}"),
            })

        # Calculate R-factors
        fc = calc_f_calc(hkl, refined, cell, sg_ops)
        fc_mag = np.abs(fc)
        fc2 = fc_mag ** 2

        r1_num = np.sum(np.abs(f_obs - np.sqrt(scale) * fc_mag))
        r1_den = np.sum(f_obs)
        r1 = r1_num / (r1_den + 1e-12)

        wr2_num = np.sum(w * (fo2 - scale * fc2) ** 2)
        wr2_den = np.sum(w * fo2 ** 2)
        wr2 = np.sqrt(wr2_num / (wr2_den + 1e-12))

        n_params = len(p0)
        n_refl = len(f_obs)
        goof = np.sqrt(np.sum(w * (fo2 - scale * fc2) ** 2) / max(n_refl - n_params, 1))

        stats = {
            "R1": float(r1),
            "wR2": float(wr2),
            "GooF": float(goof),
            "n_reflections": n_refl,
            "n_parameters": n_params,
            "scale": float(scale),
        }
        return refined, stats

    except Exception as e:
        print(f"WARNING: Refinement failed: {e}", file=sys.stderr)
        stats = {"R1": 0.99, "wR2": 0.99, "GooF": 99.0,
                 "n_reflections": len(f_obs), "n_parameters": len(p0)}
        return atoms, stats


# ---------------------------------------------------------------------------
# CIF writer
# ---------------------------------------------------------------------------

def write_cif(atoms: List[dict], cell: dict, sg_name: str,
              sg_ops: List[tuple], stats: dict, elements: List[str],
              wavelength: float, output_path: str,
              z: int = None, title: str = "SCXRD structure"):
    """Write an IUCr-compliant CIF file."""
    sg_info = lookup_sg(sg_name)
    sg_number = sg_info["number"] if sg_info else 1
    crystal_sys = sg_info["crystal_system"] if sg_info else "triclinic"

    vol = cell_volume(cell["a"], cell["b"], cell["c"],
                      cell["alpha"], cell["beta"], cell["gamma"])

    if z is None:
        z = len(sg_ops) if sg_ops else 1

    # Formula
    atom_elements = [a["element"] for a in atoms]
    formula = hill_formula(atom_elements)
    mw = molecular_weight(atom_elements)

    # Density
    density = (z * mw) / (vol * 0.6022) if vol > 0 else 0.0

    # Radiation type
    if wavelength and abs(wavelength - 0.71073) < 0.01:
        rad_type = "MoK\\a"
    elif wavelength and abs(wavelength - 1.54178) < 0.01:
        rad_type = "CuK\\a"
    else:
        rad_type = "synchrotron" if wavelength and wavelength < 0.5 else "MoK\\a"

    lines = []
    lines.append(f"data_{title.replace(' ', '_')}")
    lines.append("")
    lines.append(f"_audit_creation_method          'solve_refine_scxrd.py'")
    lines.append(f"_chemical_formula_sum            '{formula}'")
    lines.append(f"_chemical_formula_weight          {mw:.2f}")
    lines.append("")
    lines.append(f"_cell_length_a                    {cell['a']:.4f}")
    lines.append(f"_cell_length_b                    {cell['b']:.4f}")
    lines.append(f"_cell_length_c                    {cell['c']:.4f}")
    lines.append(f"_cell_angle_alpha                 {cell['alpha']:.2f}")
    lines.append(f"_cell_angle_beta                  {cell['beta']:.2f}")
    lines.append(f"_cell_angle_gamma                 {cell['gamma']:.2f}")
    lines.append(f"_cell_volume                      {vol:.2f}")
    lines.append(f"_cell_formula_units_Z             {z}")
    lines.append("")
    lines.append(f"_space_group_name_H-M_alt         '{sg_name}'")
    lines.append(f"_space_group_IT_number             {sg_number}")
    lines.append(f"_space_group_crystal_system        {crystal_sys}")
    lines.append("")
    lines.append(f"_exptl_crystal_density_diffrn      {density:.3f}")
    lines.append(f"_diffrn_radiation_type             '{rad_type}'")
    if wavelength:
        lines.append(f"_diffrn_radiation_wavelength       {wavelength:.5f}")
    lines.append("")
    lines.append(f"_refine_ls_R_factor_gt             {stats.get('R1', 0.99):.4f}")
    lines.append(f"_refine_ls_wR_factor_ref           {stats.get('wR2', 0.99):.4f}")
    lines.append(f"_refine_ls_goodness_of_fit_ref     {stats.get('GooF', 99.0):.4f}")
    lines.append(f"_refine_ls_number_reflns            {stats.get('n_reflections', 0)}")
    lines.append(f"_refine_ls_number_parameters        {stats.get('n_parameters', 0)}")
    lines.append("")

    # Symmetry operations
    lines.append("loop_")
    lines.append("_space_group_symop_operation_xyz")
    for R, t in sg_ops:
        xyz = symop_to_xyz(R, t)
        lines.append(f"'{xyz}'")
    lines.append("")

    # Atoms
    lines.append("loop_")
    lines.append("_atom_site_label")
    lines.append("_atom_site_type_symbol")
    lines.append("_atom_site_fract_x")
    lines.append("_atom_site_fract_y")
    lines.append("_atom_site_fract_z")
    lines.append("_atom_site_U_iso_or_equiv")
    lines.append("_atom_site_occupancy")
    for atom in atoms:
        label = atom.get("label", f"{atom['element']}1")
        lines.append(
            f"{label:8s} {atom['element']:4s} "
            f"{atom['x']:.5f} {atom['y']:.5f} {atom['z']:.5f} "
            f"{atom.get('uiso', 0.05):.5f} {atom.get('occ', 1.0):.4f}"
        )
    lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"CIF written to {output_path}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SCXRD structure solution & refinement pipeline")
    parser.add_argument("--hkl", required=True, help="Path to HKL file (SHELX HKLF4)")
    parser.add_argument("--ins", help="Path to INS file (optional, auto-discovered)")
    parser.add_argument("--p4p", help="Path to P4P file (optional, auto-discovered)")
    parser.add_argument("--sg", help="Space group symbol (e.g. 'P2_1/c')")
    parser.add_argument("--elements", nargs="+", help="Element symbols (e.g. C H N O)")
    parser.add_argument("--wavelength", type=float, default=0.71073,
                        help="Radiation wavelength in Å (default: 0.71073 MoKα)")
    parser.add_argument("--cell", help="Cell params as 'a,b,c,alpha,beta,gamma'")
    parser.add_argument("--grid", type=int, default=72,
                        help="Charge-flipping grid size (default: 72)")
    parser.add_argument("--trials", type=int, default=2,
                        help="Number of charge-flipping trials (default: 2)")
    parser.add_argument("--cycles", type=int, default=400,
                        help="Max charge-flipping cycles (default: 400)")
    parser.add_argument("--refine-cycles", type=int, default=50,
                        help="Least-squares refinement cycles (default: 50)")
    parser.add_argument("--output", "-o", default="structure.cif",
                        help="Output CIF path (default: structure.cif)")
    parser.add_argument("--json", help="Output JSON summary path")
    parser.add_argument("--no-shelx", action="store_true",
                        help="Skip SHELX even if available")
    args = parser.parse_args()

    # --- Step 1: Discover and parse input files ---
    print(f"=== SCXRD Pipeline ===")
    print(f"HKL: {args.hkl}")

    companions = auto_discover_files(args.hkl)
    print(f"Discovered companions: {companions}")

    ins_data = {}
    if args.ins:
        ins_data = parse_ins(args.ins)
    elif "ins" in companions:
        ins_data = parse_ins(companions["ins"])
        print(f"Auto-loaded INS: {companions['ins']}")
    elif "res" in companions:
        ins_data = parse_ins(companions["res"])
        print(f"Auto-loaded RES: {companions['res']}")

    p4p_data = {}
    if args.p4p:
        p4p_data = parse_p4p(args.p4p)
    elif "p4p" in companions:
        p4p_data = parse_p4p(companions["p4p"])
        print(f"Auto-loaded P4P: {companions['p4p']}")

    # --- Step 2: Resolve parameters ---
    # Cell parameters (priority: CLI > INS > P4P)
    cell = None
    if args.cell:
        parts = args.cell.split(",")
        cell = {
            "a": float(parts[0]), "b": float(parts[1]), "c": float(parts[2]),
            "alpha": float(parts[3]), "beta": float(parts[4]), "gamma": float(parts[5]),
        }
    elif "cell" in ins_data:
        cell = ins_data["cell"]
    elif "cell" in p4p_data:
        cell = p4p_data["cell"]

    if cell is None:
        print("ERROR: No cell parameters found. Provide --cell or INS/P4P file.", file=sys.stderr)
        sys.exit(1)

    print(f"Cell: a={cell['a']:.4f} b={cell['b']:.4f} c={cell['c']:.4f} "
          f"α={cell['alpha']:.2f} β={cell['beta']:.2f} γ={cell['gamma']:.2f}")

    # Space group
    sg_name = args.sg or p4p_data.get("space_group") or "P1"
    sg_info = lookup_sg(sg_name)
    if sg_info is None:
        print(f"WARNING: Space group '{sg_name}' not in database, using P1", file=sys.stderr)
        sg_info = SPACE_GROUPS["P1"]
        sg_name = "P1"
    sg_ops = sg_info["ops"]
    print(f"Space group: {sg_name} (#{sg_info['number']}, {sg_info['crystal_system']})")
    print(f"Symmetry operations: {len(sg_ops)}")

    # Elements
    elements = args.elements or ins_data.get("elements") or ["C"]
    print(f"Elements: {elements}")

    # Wavelength
    wavelength = args.wavelength
    if "wavelength" in ins_data:
        wavelength = ins_data["wavelength"]
    elif "wavelength" in p4p_data:
        wavelength = p4p_data["wavelength"]
    print(f"Wavelength: {wavelength:.5f} Å")

    # Z
    z = ins_data.get("z", len(sg_ops))

    # --- Step 3: Parse HKL ---
    hkl, f_obs, sigma = parse_hkl(args.hkl)
    print(f"Loaded {len(hkl)} reflections from HKL")

    if len(hkl) == 0:
        print("ERROR: No valid reflections found in HKL file.", file=sys.stderr)
        sys.exit(1)

    # --- Step 4: Structure solution ---
    atoms = None

    # Try SHELX first
    if not args.no_shelx:
        print("\n--- Trying SHELX ---")
        atoms = try_shelx(args.hkl, ins_data, sg_name, elements, cell, wavelength)
        if atoms:
            print(f"SHELX found {len(atoms)} atoms")
        else:
            print("SHELX not available or failed, using charge-flipping")

    # Charge-flipping fallback
    if not atoms:
        print(f"\n--- Charge-flipping (grid={args.grid}, trials={args.trials}, cycles={args.cycles}) ---")
        rho, r_factor = charge_flipping(
            hkl, f_obs,
            grid_size=args.grid,
            n_trials=args.trials,
            n_cycles=args.cycles,
        )
        print(f"Charge-flipping R-factor: {r_factor:.4f}")

        atoms = find_atoms_from_density(rho, cell, elements)
        print(f"Found {len(atoms)} atom positions")

        # Assign labels
        elem_count = {}
        for atom in atoms:
            e = atom["element"]
            elem_count[e] = elem_count.get(e, 0) + 1
            atom["label"] = f"{e}{elem_count[e]}"
            atom["uiso"] = 0.05
            atom["occ"] = 1.0

    if not atoms:
        print("ERROR: No atoms found. Structure solution failed.", file=sys.stderr)
        sys.exit(1)

    # --- Step 5: Refinement ---
    print(f"\n--- Least-squares refinement ({args.refine_cycles} cycles) ---")
    refined_atoms, stats = refine_atoms(
        hkl, f_obs, sigma, atoms, cell, sg_ops,
        n_cycles=args.refine_cycles,
    )
    print(f"R1 = {stats['R1']:.4f}, wR2 = {stats['wR2']:.4f}, GooF = {stats['GooF']:.4f}")
    print(f"Reflections: {stats['n_reflections']}, Parameters: {stats['n_parameters']}")

    # --- Step 6: Write CIF ---
    print(f"\n--- Writing CIF to {args.output} ---")
    write_cif(
        refined_atoms, cell, sg_name, sg_ops, stats, elements,
        wavelength, args.output, z=z,
        title=ins_data.get("title", "SCXRD_structure"),
    )

    # --- Step 7: JSON summary ---
    summary = {
        "success": True,
        "space_group": sg_name,
        "space_group_number": sg_info["number"],
        "crystal_system": sg_info["crystal_system"],
        "cell": cell,
        "volume": cell_volume(cell["a"], cell["b"], cell["c"],
                              cell["alpha"], cell["beta"], cell["gamma"]),
        "wavelength": wavelength,
        "Z": z,
        "n_atoms": len(refined_atoms),
        "elements": elements,
        "formula": hill_formula([a["element"] for a in refined_atoms]),
        "molecular_weight": molecular_weight([a["element"] for a in refined_atoms]),
        "R1": stats["R1"],
        "wR2": stats["wR2"],
        "GooF": stats["GooF"],
        "n_reflections": stats["n_reflections"],
        "n_parameters": stats["n_parameters"],
        "cif_path": args.output,
        "atoms": [
            {"label": a.get("label", ""), "element": a["element"],
             "x": round(a["x"], 5), "y": round(a["y"], 5), "z": round(a["z"], 5),
             "uiso": round(a.get("uiso", 0.05), 5), "occ": round(a.get("occ", 1.0), 4)}
            for a in refined_atoms
        ],
    }

    if args.json:
        with open(args.json, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"JSON summary written to {args.json}")

    # Print summary to stdout
    print(f"\n=== Result Summary ===")
    print(f"Formula: {summary['formula']}")
    print(f"Space group: {sg_name} ({sg_info['crystal_system']})")
    print(f"Cell: {cell['a']:.4f} {cell['b']:.4f} {cell['c']:.4f} "
          f"{cell['alpha']:.2f} {cell['beta']:.2f} {cell['gamma']:.2f}")
    print(f"Volume: {summary['volume']:.2f} Å³")
    print(f"Z = {z}")
    print(f"R1 = {stats['R1']:.4f}, wR2 = {stats['wR2']:.4f}, GooF = {stats['GooF']:.4f}")
    print(f"Atoms: {len(refined_atoms)}")
    print(f"CIF: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
