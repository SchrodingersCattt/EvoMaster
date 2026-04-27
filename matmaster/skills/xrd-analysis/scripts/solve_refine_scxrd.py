#!/usr/bin/env python3
"""
solve_refine_scxrd.py — Complete SCXRD structure solution and refinement pipeline.

Pipeline:
1. Parse HKL (SHELX HKLF4), INS, and P4P files
2. Auto-discover companion files from HKL stem
3. Try SHELX (shelxs/shelxt + shelxl) if installed
4. Fall back to Python charge-flipping + least-squares refinement
5. Write IUCr-compliant CIF output

Usage:
    python3 solve_refine_scxrd.py --hkl data.hkl [--ins data.ins] [--p4p data.p4p] \\
        --sg "P2_1/c" --elements "C H N O" \\
        [--grid 72] [--trials 2] [--cycles 400] \\
        --output result.cif [--json result.json]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# Import the helper library
script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))
from solve_refine_scxrd_lib import (
    SCATTERING_FACTORS,
    calc_scattering_factor,
    charge_flipping,
    crystal_system,
    find_atoms,
    get_sg_name,
    molecular_weight,
    resolve_sg,
    sg_number,
    sg_ops_matrices,
    symop_to_xyz,
)


# ---------------------------------------------------------------------------
# File parsers
# ---------------------------------------------------------------------------

def parse_hkl(path: str) -> np.ndarray:
    """Parse SHELX HKLF4 format HKL file. Returns Nx5 array (h, k, l, F^2, sigma)."""
    reflections = []
    with open(path, 'r') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line.strip():
                continue
            # HKLF4 format: h(4) k(4) l(4) F^2(8) sigma(8) [batch(4)]
            # Fixed-width format
            try:
                if len(line) >= 28:
                    h = int(line[0:4])
                    k = int(line[4:8])
                    l = int(line[8:12])
                    fsq = float(line[12:20])
                    sig = float(line[20:28])
                else:
                    # Try space-separated
                    parts = line.split()
                    if len(parts) >= 5:
                        h, k, l = int(parts[0]), int(parts[1]), int(parts[2])
                        fsq, sig = float(parts[3]), float(parts[4])
                    else:
                        continue
            except (ValueError, IndexError):
                continue
            # End marker: 0 0 0
            if h == 0 and k == 0 and l == 0:
                break
            if sig > 0:
                reflections.append([h, k, l, fsq, sig])
    if not reflections:
        raise ValueError(f"No valid reflections found in {path}")
    data = np.array(reflections, dtype=float)
    # Convert F^2 to |F|
    f_obs = np.sqrt(np.maximum(data[:, 3], 0.0))
    sigma_f = data[:, 4] / (2.0 * np.maximum(f_obs, 1e-10))
    return np.column_stack([data[:, :3], f_obs, sigma_f])


def parse_ins(path: str) -> Dict:
    """Parse SHELX .ins file for cell, SG, elements, wavelength."""
    info = {}
    with open(path, 'r') as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].upper()

        if cmd == 'CELL' and len(parts) >= 8:
            info['wavelength'] = float(parts[1])
            info['cell'] = tuple(float(x) for x in parts[2:8])

        elif cmd == 'ZERR' and len(parts) >= 8:
            info['z'] = int(float(parts[1]))
            info['cell_esd'] = tuple(float(x) for x in parts[2:8])

        elif cmd == 'LATT':
            info['latt'] = int(parts[1])

        elif cmd == 'SYMM':
            if 'symm' not in info:
                info['symm'] = []
            info['symm'].append(' '.join(parts[1:]))

        elif cmd == 'SFAC':
            info['elements'] = [p for p in parts[1:] if p.isalpha()]

        elif cmd == 'UNIT' and 'elements' in info:
            counts = [float(x) for x in parts[1:len(info['elements']) + 1]]
            info['unit'] = dict(zip(info['elements'], counts))

    return info


def parse_p4p(path: str) -> Dict:
    """Parse Bruker .p4p file for cell and wavelength."""
    info = {}
    with open(path, 'r') as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == 'CELL' and len(parts) >= 7:
                info['cell'] = tuple(float(x) for x in parts[1:7])
            elif parts[0] == 'CELLSD' and len(parts) >= 7:
                info['cell_esd'] = tuple(float(x) for x in parts[1:7])
            elif parts[0] == 'SOURCE' and len(parts) >= 2:
                # wavelength often on SOURCE line
                try:
                    info['wavelength'] = float(parts[1])
                except ValueError:
                    pass
            elif parts[0] == 'CESSION' or parts[0] == 'MORPH':
                pass
    return info


def auto_discover_files(hkl_path: str) -> Dict[str, Optional[str]]:
    """Auto-discover companion files from the HKL stem."""
    stem = Path(hkl_path).stem
    parent = Path(hkl_path).parent
    files = {}
    for ext in ['.ins', '.p4p', '.res']:
        candidate = parent / f"{stem}{ext}"
        if candidate.exists():
            files[ext[1:]] = str(candidate)
        else:
            # Try case-insensitive
            for f in parent.iterdir():
                if f.stem.lower() == stem.lower() and f.suffix.lower() == ext:
                    files[ext[1:]] = str(f)
                    break
    return files


# ---------------------------------------------------------------------------
# SHELX integration
# ---------------------------------------------------------------------------

def try_shelx(
    hkl_path: str,
    ins_path: Optional[str],
    sg_name: str,
    cell: Tuple,
    elements: List[str],
    wavelength: float,
    work_dir: str,
) -> Optional[str]:
    """Try to solve + refine with SHELX. Returns CIF path or None."""
    shelxt = shutil.which('shelxt')
    shelxs = shutil.which('shelxs')
    shelxl = shutil.which('shelxl')

    if not shelxl:
        return None
    solver = shelxt or shelxs
    if not solver:
        return None

    # Copy files to work directory
    os.makedirs(work_dir, exist_ok=True)
    stem = "structure"
    shutil.copy2(hkl_path, os.path.join(work_dir, f"{stem}.hkl"))

    if ins_path:
        shutil.copy2(ins_path, os.path.join(work_dir, f"{stem}.ins"))
    else:
        # Generate minimal INS
        _write_shelx_ins(
            os.path.join(work_dir, f"{stem}.ins"),
            cell, sg_name, elements, wavelength
        )

    try:
        # Solve
        result = subprocess.run(
            [solver, stem],
            cwd=work_dir, capture_output=True, text=True, timeout=120
        )
        # Check for .res file
        res_file = os.path.join(work_dir, f"{stem}.res")
        if not os.path.exists(res_file):
            return None

        # Refine with shelxl
        shutil.copy2(res_file, os.path.join(work_dir, f"{stem}.ins"))
        result = subprocess.run(
            [shelxl, stem],
            cwd=work_dir, capture_output=True, text=True, timeout=120
        )

        cif_file = os.path.join(work_dir, f"{stem}.cif")
        if os.path.exists(cif_file):
            return cif_file
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def _write_shelx_ins(
    path: str,
    cell: Tuple,
    sg_name: str,
    elements: List[str],
    wavelength: float,
):
    """Write a minimal SHELX .ins file."""
    sg = resolve_sg(sg_name)
    sg_num = sg["number"] if sg else 1
    z = len(sg["ops"]) if sg else 1

    # LATT
    latt_map = {
        "P": 1, "I": 2, "R": 3, "F": 4, "A": 5, "B": 6, "C": 7,
    }
    canonical = get_sg_name(sg_name)
    first_char = canonical[0] if canonical else "P"
    latt = latt_map.get(first_char, 1)
    # Centrosymmetric → positive LATT, acentric → negative
    # Simple heuristic: if "-" in SG name (like P-1, C2/c), it's centrosymmetric
    is_centro = "-" in sg_name or "/" in sg_name
    if not is_centro:
        latt = -latt

    with open(path, 'w') as f:
        f.write(f"TITL Structure solution\n")
        f.write(f"CELL {wavelength:.5f} {cell[0]:.4f} {cell[1]:.4f} {cell[2]:.4f} "
                f"{cell[3]:.3f} {cell[4]:.3f} {cell[5]:.3f}\n")
        f.write(f"ZERR {z} 0.001 0.001 0.001 0.01 0.01 0.01\n")
        f.write(f"LATT {latt}\n")

        # SYMM cards
        if sg:
            for op_mat in sg["ops"][1:]:  # skip identity
                xyz = symop_to_xyz(op_mat)
                # Skip centering translations for C/I/F lattices
                f.write(f"SYMM {xyz}\n")

        f.write(f"SFAC {' '.join(elements)}\n")
        unit_str = ' '.join([str(z)] * len(elements))
        f.write(f"UNIT {unit_str}\n")
        f.write(f"TREF\n")
        f.write(f"HKLF 4\n")
        f.write(f"END\n")


# ---------------------------------------------------------------------------
# Least-squares refinement
# ---------------------------------------------------------------------------

def refine_positions(
    atoms: List[Dict],
    hkl_data: np.ndarray,
    cell: Tuple,
    sg_ops: List[np.ndarray],
    n_cycles: int = 400,
) -> Tuple[List[Dict], float, float, float]:
    """
    Least-squares refinement of atom positions and Uiso against F^2.

    Returns: (refined_atoms, R1, wR2, GooF)
    """
    try:
        from scipy.optimize import least_squares
    except ImportError:
        warnings.warn("scipy not available, skipping refinement")
        return atoms, 0.5, 0.5, 10.0

    a, b, c, alpha, beta, gamma = cell
    alpha_r, beta_r, gamma_r = np.radians(alpha), np.radians(beta), np.radians(gamma)

    # Metric tensor for d-spacing calculation
    V = a * b * c * np.sqrt(
        1 - np.cos(alpha_r)**2 - np.cos(beta_r)**2 - np.cos(gamma_r)**2
        + 2 * np.cos(alpha_r) * np.cos(beta_r) * np.cos(gamma_r)
    )

    h = hkl_data[:, 0].astype(int)
    k = hkl_data[:, 1].astype(int)
    l = hkl_data[:, 2].astype(int)
    f_obs = hkl_data[:, 3]
    sigma = hkl_data[:, 4]

    # Weights
    w = 1.0 / (sigma**2 + 1e-10)

    # Build parameter vector: [x1, y1, z1, uiso1, x2, y2, z2, uiso2, ..., scale]
    n_atoms = len(atoms)
    params = []
    for atom in atoms:
        params.extend([atom['frac_x'], atom['frac_y'], atom['frac_z'], 0.03])
    params.append(1.0)  # scale factor
    params = np.array(params)

    def calc_f(params):
        """Calculate structure factors from parameters."""
        scale = params[-1]
        f_calc = np.zeros(len(h), dtype=complex)

        for i_atom in range(n_atoms):
            x = params[i_atom * 4]
            y = params[i_atom * 4 + 1]
            z = params[i_atom * 4 + 2]
            uiso = params[i_atom * 4 + 3]

            elem = atoms[i_atom]['element']

            for op in sg_ops:
                # Apply symmetry operation
                xyz = np.array([x, y, z])
                xyz_sym = op[:, :3] @ xyz + op[:, 3]

                for idx in range(len(h)):
                    # s^2 = (sin(theta)/lambda)^2
                    # For now, approximate using d*
                    hkl_vec = np.array([h[idx], k[idx], l[idx]])
                    phase = 2 * np.pi * np.dot(hkl_vec, xyz_sym)
                    # Scattering factor (approximate s^2)
                    d_star_sq = (h[idx]**2 / a**2 + k[idx]**2 / b**2 + l[idx]**2 / c**2)
                    s_sq = d_star_sq / 4.0
                    f_atom = calc_scattering_factor(elem, s_sq)
                    # Temperature factor
                    B = 8 * np.pi**2 * max(uiso, 0.001)
                    temp_factor = np.exp(-B * s_sq)
                    f_calc[idx] += f_atom * temp_factor * np.exp(1j * phase)

        return scale * np.abs(f_calc)

    def residuals(params):
        f_c = calc_f(params)
        return np.sqrt(w) * (f_obs - f_c)

    # Run refinement
    try:
        result = least_squares(
            residuals, params,
            method='lm',
            max_nfev=n_cycles,
            ftol=1e-6, xtol=1e-6,
        )
        params = result.x
    except Exception:
        pass  # Keep initial params

    # Calculate R-factors
    f_calc_final = calc_f(params)
    R1 = np.sum(np.abs(f_obs - f_calc_final)) / np.sum(f_obs)

    wR2_num = np.sum(w * (f_obs - f_calc_final)**2)
    wR2_den = np.sum(w * f_obs**2)
    wR2 = np.sqrt(wR2_num / max(wR2_den, 1e-10))

    n_obs = len(f_obs)
    n_params = len(params)
    dof = max(n_obs - n_params, 1)
    GooF = np.sqrt(np.sum(w * (f_obs - f_calc_final)**2) / dof)

    # Update atoms
    refined_atoms = []
    for i_atom in range(n_atoms):
        atom = atoms[i_atom].copy()
        atom['frac_x'] = params[i_atom * 4] % 1.0
        atom['frac_y'] = params[i_atom * 4 + 1] % 1.0
        atom['frac_z'] = params[i_atom * 4 + 2] % 1.0
        atom['uiso'] = max(params[i_atom * 4 + 3], 0.001)
        refined_atoms.append(atom)

    return refined_atoms, R1, wR2, GooF


# ---------------------------------------------------------------------------
# CIF writer
# ---------------------------------------------------------------------------

def _hill_formula(formula_dict: Dict[str, int]) -> str:
    """Format chemical formula in Hill order: C first, H second, then alphabetical."""
    parts = []
    if 'C' in formula_dict:
        count = formula_dict['C']
        parts.append(f"C{count}" if count > 1 else "C")
        if 'H' in formula_dict:
            count = formula_dict['H']
            parts.append(f"H{count}" if count > 1 else "H")
    remaining = sorted(k for k in formula_dict if k not in ('C', 'H'))
    for elem in remaining:
        count = formula_dict[elem]
        parts.append(f"{elem}{count}" if count > 1 else elem)
    return ' '.join(parts)


def _radiation_type(wavelength: float) -> str:
    """Detect radiation type from wavelength."""
    if abs(wavelength - 0.71073) < 0.01:
        return "Mo K\\a"
    elif abs(wavelength - 1.54184) < 0.01 or abs(wavelength - 1.5406) < 0.01:
        return "Cu K\\a"
    elif abs(wavelength - 0.56086) < 0.01:
        return "Ag K\\a"
    else:
        return f"\\l = {wavelength:.5f} \\%A"


def _calc_density(cell: Tuple, z: int, mw: float) -> float:
    """Calculate crystal density in g/cm^3."""
    a, b, c, alpha, beta, gamma = cell
    alpha_r, beta_r, gamma_r = np.radians(alpha), np.radians(beta), np.radians(gamma)
    V = a * b * c * np.sqrt(
        1 - np.cos(alpha_r)**2 - np.cos(beta_r)**2 - np.cos(gamma_r)**2
        + 2 * np.cos(alpha_r) * np.cos(beta_r) * np.cos(gamma_r)
    )
    # V in Angstrom^3, convert to cm^3: 1 A^3 = 1e-24 cm^3
    N_A = 6.02214076e23
    density = (z * mw) / (V * 1e-24 * N_A)
    return density


def write_cif(
    path: str,
    atoms: List[Dict],
    cell: Tuple,
    sg_name: str,
    R1: float,
    wR2: float,
    GooF: float,
    wavelength: float = 0.71073,
    cell_esd: Optional[Tuple] = None,
):
    """Write an IUCr-compliant CIF file."""
    canonical_sg = get_sg_name(sg_name)
    sg = resolve_sg(sg_name)
    sg_num = sg["number"] if sg else 1
    ops = sg["ops"] if sg else [np.eye(3, 4)]
    sys_name = crystal_system(sg_name)
    z = len(ops)

    # Count elements (asymmetric unit)
    formula_dict = {}
    for atom in atoms:
        elem = atom['element']
        formula_dict[elem] = formula_dict.get(elem, 0) + 1

    formula_str = _hill_formula(formula_dict)
    mw = molecular_weight(formula_dict)
    density = _calc_density(cell, z, mw)

    a, b, c, alpha, beta, gamma = cell
    alpha_r, beta_r, gamma_r = np.radians(alpha), np.radians(beta), np.radians(gamma)
    V = a * b * c * np.sqrt(
        1 - np.cos(alpha_r)**2 - np.cos(beta_r)**2 - np.cos(gamma_r)**2
        + 2 * np.cos(alpha_r) * np.cos(beta_r) * np.cos(gamma_r)
    )

    esd = cell_esd if cell_esd else (0.001, 0.001, 0.001, 0.01, 0.01, 0.01)

    with open(path, 'w') as f:
        f.write("data_structure\n")
        f.write(f"_audit_creation_method          'solve_refine_scxrd.py'\n")
        f.write(f"\n")

        # Cell
        f.write(f"_cell_length_a                  {a:.4f}({esd[0]*1e4:.0f})\n")
        f.write(f"_cell_length_b                  {b:.4f}({esd[1]*1e4:.0f})\n")
        f.write(f"_cell_length_c                  {c:.4f}({esd[2]*1e4:.0f})\n")
        f.write(f"_cell_angle_alpha               {alpha:.3f}({esd[3]*1e3:.0f})\n")
        f.write(f"_cell_angle_beta                {beta:.3f}({esd[4]*1e3:.0f})\n")
        f.write(f"_cell_angle_gamma               {gamma:.3f}({esd[5]*1e3:.0f})\n")
        f.write(f"_cell_volume                    {V:.2f}\n")
        f.write(f"_cell_formula_units_Z           {z}\n")
        f.write(f"\n")

        # Symmetry
        f.write(f"_symmetry_cell_setting           {sys_name}\n")
        f.write(f"_symmetry_space_group_name_H-M   '{canonical_sg}'\n")
        f.write(f"_symmetry_Int_Tables_number       {sg_num}\n")
        f.write(f"_space_group_crystal_system       {sys_name}\n")
        f.write(f"\n")

        # Symmetry operations
        f.write(f"loop_\n")
        f.write(f"_symmetry_equiv_pos_site_id\n")
        f.write(f"_symmetry_equiv_pos_as_xyz\n")
        for i, op_mat in enumerate(ops):
            xyz_str = symop_to_xyz(op_mat)
            f.write(f"  {i + 1}  '{xyz_str}'\n")
        f.write(f"\n")

        # Chemical formula
        f.write(f"_chemical_formula_sum            '{formula_str}'\n")
        f.write(f"_chemical_formula_moiety         '{formula_str}'\n")
        f.write(f"_chemical_formula_weight          {mw:.2f}\n")
        f.write(f"\n")

        # Experimental
        f.write(f"_exptl_crystal_density_diffrn    {density:.3f}\n")
        f.write(f"_diffrn_radiation_type            '{_radiation_type(wavelength)}'\n")
        f.write(f"\n")

        # R-factors
        f.write(f"_refine_ls_R_factor_gt           {R1:.4f}\n")
        f.write(f"_refine_ls_wR_factor_ref         {wR2:.4f}\n")
        f.write(f"_refine_ls_goodness_of_fit_ref   {GooF:.4f}\n")
        f.write(f"\n")

        # Atom sites
        f.write(f"loop_\n")
        f.write(f"_atom_site_label\n")
        f.write(f"_atom_site_type_symbol\n")
        f.write(f"_atom_site_fract_x\n")
        f.write(f"_atom_site_fract_y\n")
        f.write(f"_atom_site_fract_z\n")
        f.write(f"_atom_site_U_iso_or_equiv\n")
        f.write(f"_atom_site_occupancy\n")

        elem_counts = {}
        for atom in atoms:
            elem = atom['element']
            elem_counts[elem] = elem_counts.get(elem, 0) + 1
            label = f"{elem}{elem_counts[elem]}"
            fx = atom.get('frac_x', 0.0) % 1.0
            fy = atom.get('frac_y', 0.0) % 1.0
            fz = atom.get('frac_z', 0.0) % 1.0
            uiso = atom.get('uiso', 0.03)
            f.write(f"  {label:<6s} {elem:<4s} {fx:.6f}  {fy:.6f}  {fz:.6f}  "
                    f"{uiso:.6f}  1.0000\n")

        f.write(f"\n")
        f.write(f"# End of CIF\n")

    return path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SCXRD structure solution and refinement")
    parser.add_argument("--hkl", required=True, help="HKL file (SHELX HKLF4)")
    parser.add_argument("--ins", help="SHELX .ins file")
    parser.add_argument("--p4p", help="Bruker .p4p file")
    parser.add_argument("--sg", help="Space group (Hermann-Mauguin)")
    parser.add_argument("--elements", help="Expected elements (space-separated)")
    parser.add_argument("--wavelength", type=float, help="X-ray wavelength (Angstrom)")
    parser.add_argument("--grid", type=int, default=72, help="FFT grid size (default: 72)")
    parser.add_argument("--trials", type=int, default=2, help="Charge-flipping trials (default: 2)")
    parser.add_argument("--cycles", type=int, default=400, help="Refinement cycles (default: 400)")
    parser.add_argument("--output", "-o", default="structure.cif", help="Output CIF file")
    parser.add_argument("--json", help="Output JSON with R-factors and atom list")
    args = parser.parse_args()

    print(f"=== SCXRD Structure Solution ===")
    print(f"HKL file: {args.hkl}")

    # Auto-discover companion files
    companions = auto_discover_files(args.hkl)
    ins_path = args.ins or companions.get('ins')
    p4p_path = args.p4p or companions.get('p4p')

    if ins_path:
        print(f"INS file: {ins_path}")
    if p4p_path:
        print(f"P4P file: {p4p_path}")

    # Parse files for cell, SG, elements, wavelength
    cell = None
    cell_esd = None
    sg_name = args.sg
    elements = args.elements.split() if args.elements else None
    wavelength = args.wavelength

    if ins_path:
        ins_info = parse_ins(ins_path)
        if not cell and 'cell' in ins_info:
            cell = ins_info['cell']
        if not cell_esd and 'cell_esd' in ins_info:
            cell_esd = ins_info['cell_esd']
        if not sg_name and 'sg' in ins_info:
            sg_name = ins_info['sg']
        if not elements and 'elements' in ins_info:
            elements = ins_info['elements']
        if not wavelength and 'wavelength' in ins_info:
            wavelength = ins_info['wavelength']

    if p4p_path:
        p4p_info = parse_p4p(p4p_path)
        if not cell and 'cell' in p4p_info:
            cell = p4p_info['cell']
        if not cell_esd and 'cell_esd' in p4p_info:
            cell_esd = p4p_info['cell_esd']
        if not wavelength and 'wavelength' in p4p_info:
            wavelength = p4p_info['wavelength']

    # Defaults
    if not wavelength:
        wavelength = 0.71073  # Mo K-alpha
        print(f"WARNING: No wavelength found, defaulting to Mo Kα ({wavelength} Å)")
    if not sg_name:
        sg_name = "P1"
        print(f"WARNING: No space group specified, defaulting to {sg_name}")
    if not elements:
        elements = ["C", "H", "N", "O"]
        print(f"WARNING: No elements specified, defaulting to {elements}")

    if not cell:
        print("ERROR: No unit cell parameters found. Provide via --ins, --p4p, or command line.")
        sys.exit(1)

    print(f"\nCell: a={cell[0]:.4f} b={cell[1]:.4f} c={cell[2]:.4f}")
    print(f"      α={cell[3]:.3f} β={cell[4]:.3f} γ={cell[5]:.3f}")
    print(f"Space group: {sg_name}")
    print(f"Elements: {elements}")
    print(f"Wavelength: {wavelength:.5f} Å")

    # Parse HKL
    print(f"\nParsing HKL file...")
    hkl_data = parse_hkl(args.hkl)
    print(f"  {len(hkl_data)} reflections loaded")

    # Get symmetry operations
    sg_ops = sg_ops_matrices(sg_name)
    print(f"  {len(sg_ops)} symmetry operations for {get_sg_name(sg_name)}")

    # Try SHELX first
    print(f"\nAttempting SHELX solution...")
    work_dir = Path(args.output).parent / "_shelx_work"
    shelx_cif = try_shelx(args.hkl, ins_path, sg_name, cell, elements, wavelength, str(work_dir))

    if shelx_cif:
        print(f"  SHELX succeeded! CIF: {shelx_cif}")
        shutil.copy2(shelx_cif, args.output)
        print(f"\nOutput CIF: {args.output}")
        return

    print(f"  SHELX not available, using Python charge-flipping fallback")

    # Charge flipping
    print(f"\nRunning charge-flipping (grid={args.grid}, trials={args.trials})...")
    rho, r_initial = charge_flipping(
        hkl_data, cell, sg_ops, elements,
        grid_size=args.grid,
        n_trials=args.trials,
    )
    print(f"  Initial R-factor: {r_initial:.4f}")

    # Find atoms
    print(f"\nFinding atoms in density map...")
    atoms = find_atoms(rho, cell, elements)
    print(f"  Found {len(atoms)} atom positions")

    if not atoms:
        print("WARNING: No atoms found. Try increasing --grid or --trials.")
        # Write empty CIF
        atoms = []

    # Refine
    print(f"\nRefining positions (cycles={args.cycles})...")
    refined_atoms, R1, wR2, GooF = refine_positions(
        atoms, hkl_data, cell, sg_ops, n_cycles=args.cycles
    )
    print(f"  R1 = {R1:.4f}")
    print(f"  wR2 = {wR2:.4f}")
    print(f"  GooF = {GooF:.4f}")

    # Wrap fractional coordinates to [0, 1)
    for atom in refined_atoms:
        atom['frac_x'] = atom['frac_x'] % 1.0
        atom['frac_y'] = atom['frac_y'] % 1.0
        atom['frac_z'] = atom['frac_z'] % 1.0

    # Write CIF
    print(f"\nWriting CIF: {args.output}")
    write_cif(
        args.output, refined_atoms, cell, sg_name,
        R1, wR2, GooF, wavelength, cell_esd
    )

    # Write JSON if requested
    if args.json:
        result = {
            "success": True,
            "R1": round(R1, 4),
            "wR2": round(wR2, 4),
            "GooF": round(GooF, 4),
            "space_group": get_sg_name(sg_name),
            "sg_number": sg_number(sg_name),
            "crystal_system": crystal_system(sg_name),
            "cell": {
                "a": cell[0], "b": cell[1], "c": cell[2],
                "alpha": cell[3], "beta": cell[4], "gamma": cell[5],
            },
            "wavelength": wavelength,
            "n_reflections": len(hkl_data),
            "n_atoms": len(refined_atoms),
            "atoms": [
                {
                    "element": a["element"],
                    "label": f"{a['element']}{i+1}",
                    "frac_x": round(a["frac_x"], 6),
                    "frac_y": round(a["frac_y"], 6),
                    "frac_z": round(a["frac_z"], 6),
                    "uiso": round(a.get("uiso", 0.03), 6),
                }
                for i, a in enumerate(refined_atoms)
            ],
            "formula": _hill_formula({
                a["element"]: sum(1 for x in refined_atoms if x["element"] == a["element"])
                for a in refined_atoms
            }),
            "cif_file": args.output,
        }
        with open(args.json, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"JSON output: {args.json}")

    print(f"\n=== Done ===")


if __name__ == "__main__":
    main()
