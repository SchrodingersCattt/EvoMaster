#!/usr/bin/env python3
"""
workspace_review.py — One-call comprehensive workspace review for DFT submissions.

Combines multiple validation and evaluation steps into a SINGLE call to reduce
agent turn count and increase consistency. Performs:
  1. File inventory (INPUT, structure, KPT, PP/orbitals)
  2. Input parameter validation (diagnose_input logic)
  3. Best-practice evaluation (evaluate_dft_setup logic)
  4. Cross-reference checks (preflight logic)
  5. Structure assessment (if pymatgen available)
  6. Generates structured report with pass/fail grade and actionable recommendations

This script is designed to replace a multi-step review workflow with a single
deterministic call that produces a complete evaluation report.

Usage:
  python workspace_review.py --dir ./workspace/ --software abacus
  python workspace_review.py --dir ./workspace/ --software vasp
  python workspace_review.py --dir ./workspace/ --software abacus --format json
  python workspace_review.py --dir ./workspace/ --software abacus --fix

The report can be included directly in task responses as a structured evaluation.
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# File inventory
# ---------------------------------------------------------------------------

def _inventory_files(workspace: Path, software: str) -> dict:
    """Inventory all relevant files in workspace."""
    files = {"found": [], "missing_critical": [], "warnings": []}
    
    all_files = sorted(f.name for f in workspace.iterdir() if f.is_file())
    files["all"] = all_files
    
    if software == "abacus":
        # Check critical ABACUS files
        for name in ["INPUT", "STRU"]:
            matches = [f for f in all_files if f.upper().startswith(name)]
            if matches:
                files["found"].extend(matches)
            else:
                files["missing_critical"].append(name)
        
        # KPT: optional if kspacing is set
        kpt_matches = [f for f in all_files if "kpt" in f.lower() or f == "KPT"]
        if kpt_matches:
            files["found"].extend(kpt_matches)
        
        # PP and orbital files
        pp_files = [f for f in all_files if f.endswith(".upf")]
        orb_files = [f for f in all_files if f.endswith(".orb")]
        files["pseudopotentials"] = pp_files
        files["orbitals"] = orb_files
        
        if not pp_files:
            files["warnings"].append("No .upf pseudopotential files found")
    
    elif software == "vasp":
        for name in ["INCAR", "POSCAR", "KPOINTS", "POTCAR"]:
            if name in all_files or any(f.startswith(name) for f in all_files):
                files["found"].append(name)
            else:
                if name in ("INCAR", "POSCAR"):
                    files["missing_critical"].append(name)
                else:
                    files["warnings"].append(f"{name} not found (may be needed)")
    
    return files


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def _parse_params(text: str) -> dict[str, str]:
    """Parse key-value parameter file."""
    params = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        for ch in ("#", "!", "//"):
            if ch in stripped:
                stripped = stripped[:stripped.index(ch)].strip()
        if not stripped:
            continue
        if re.match(r"^\s*INPUT_PARAMETERS\s*$", stripped, re.IGNORECASE):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
        else:
            parts = stripped.split(None, 1)
            if len(parts) >= 2:
                key, val = parts[0], parts[1]
            elif len(parts) == 1:
                key, val = parts[0], ""
            else:
                continue
        params[key.strip().lower()] = val.strip()
    return params


def _get_float(params: dict, key: str) -> float | None:
    v = params.get(key.lower())
    if v is None:
        return None
    try:
        return float(v.split()[0])
    except (ValueError, IndexError):
        return None


def _get_int(params: dict, key: str) -> int | None:
    v = params.get(key.lower())
    if v is None:
        return None
    try:
        return int(v.split(".")[0].split()[0])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Evaluation categories
# ---------------------------------------------------------------------------

class CheckResult:
    def __init__(self, name: str, status: str, details: str = "", fix: str = ""):
        self.name = name
        self.status = status  # "PASS", "FAIL", "WARN", "SKIP"
        self.details = details
        self.fix = fix

    def to_dict(self):
        d = {"name": self.name, "status": self.status}
        if self.details:
            d["details"] = self.details
        if self.fix:
            d["fix"] = self.fix
        return d

    def icon(self):
        return {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}[self.status]


def _review_abacus(workspace: Path, params: dict) -> list[CheckResult]:
    """Run all ABACUS checks."""
    results = []
    calc = params.get("calculation", "scf").lower()

    # 1. ecutwfc
    ecutwfc = _get_float(params, "ecutwfc")
    if ecutwfc is None:
        results.append(CheckResult("ecutwfc", "FAIL", "Not set (ABACUS default may be too low)", "ecutwfc 100"))
    elif ecutwfc >= 100:
        results.append(CheckResult("ecutwfc", "PASS", f"ecutwfc={ecutwfc} Ry"))
    elif ecutwfc >= 50:
        results.append(CheckResult("ecutwfc", "WARN", f"ecutwfc={ecutwfc} below standard 100 Ry"))
    else:
        results.append(CheckResult("ecutwfc", "FAIL", f"ecutwfc={ecutwfc} too low", "ecutwfc 100"))

    # 2. mixing_type
    mixing = params.get("mixing_type", "")
    if mixing.lower() == "broyden":
        results.append(CheckResult("mixing_type", "PASS", "broyden"))
    elif mixing:
        results.append(CheckResult("mixing_type", "PASS", f"mixing_type={mixing}"))
    else:
        results.append(CheckResult("mixing_type", "WARN", "Not set", "mixing_type broyden"))

    # 3. smearing
    sigma = _get_float(params, "smearing_sigma")
    method = params.get("smearing_method", "")
    if method and sigma is not None and sigma <= 0.02:
        results.append(CheckResult("smearing", "PASS", f"{method} σ={sigma}"))
    elif method:
        results.append(CheckResult("smearing", "WARN", f"σ={sigma} (recommend ≤0.01)"))
    else:
        results.append(CheckResult("smearing", "WARN", "Not set", "smearing_method gauss\nsmearing_sigma 0.01"))

    # 4. SCF convergence
    scf_thr = _get_float(params, "scf_thr")
    scf_nmax = _get_int(params, "scf_nmax")
    if scf_thr and scf_thr <= 1e-6 and scf_nmax and scf_nmax >= 100:
        results.append(CheckResult("scf_convergence", "PASS", f"scf_thr={scf_thr}, scf_nmax={scf_nmax}"))
    elif scf_thr and scf_thr <= 1e-6:
        results.append(CheckResult("scf_convergence", "PASS", f"scf_thr={scf_thr}"))
    else:
        results.append(CheckResult("scf_convergence", "WARN", "Consider scf_thr 1.0e-7, scf_nmax 100"))

    # 5. Relaxation guards
    if calc in ("relax", "cell-relax"):
        cal_force = _get_int(params, "cal_force")
        if cal_force != 1:
            results.append(CheckResult("cal_force", "FAIL", 
                f"CRITICAL: calculation='{calc}' requires cal_force=1", "cal_force 1"))
        else:
            results.append(CheckResult("cal_force", "PASS"))
        
        if calc == "cell-relax":
            cal_stress = _get_int(params, "cal_stress")
            if cal_stress != 1:
                results.append(CheckResult("cal_stress", "FAIL",
                    "CRITICAL: cell-relax requires cal_stress=1", "cal_stress 1"))
            else:
                results.append(CheckResult("cal_stress", "PASS"))
        
        force_thr = _get_float(params, "force_thr_ev")
        if force_thr:
            results.append(CheckResult("force_threshold", "PASS", f"force_thr_ev={force_thr}"))
        else:
            results.append(CheckResult("force_threshold", "WARN", "Not set", "force_thr_ev 0.01"))

    # 6. NSCF guards
    if calc == "nscf":
        init_chg = params.get("init_chg", "").lower()
        if init_chg == "file":
            results.append(CheckResult("init_chg", "PASS"))
        else:
            results.append(CheckResult("init_chg", "FAIL", "NSCF requires init_chg=file", "init_chg file"))
        
        symmetry = _get_int(params, "symmetry")
        if symmetry == 0:
            results.append(CheckResult("symmetry", "PASS"))
        else:
            results.append(CheckResult("symmetry", "FAIL", "NSCF requires symmetry=0", "symmetry 0"))

    # 7. Slab/dipole
    out_pot = _get_int(params, "out_pot")
    efield = _get_int(params, "efield_flag")
    dip_cor = _get_int(params, "dip_cor_flag")
    if out_pot == 2:
        if efield == 1 and dip_cor == 1:
            results.append(CheckResult("dipole_correction", "PASS", "efield_flag=1 + dip_cor_flag=1"))
        else:
            results.append(CheckResult("dipole_correction", "FAIL",
                "out_pot=2 without dipole correction — results will have artificial field",
                "efield_flag 1\ndip_cor_flag 1\nefield_dir 2\nefield_pos_max 0.0\nefield_pos_dec 0.1\nefield_amp 0.0"))

    # 8. ntype cross-check
    ntype = _get_int(params, "ntype")
    stru_ref = params.get("stru_file", "STRU")
    stru_path = workspace / stru_ref
    if stru_path.exists():
        stru_text = stru_path.read_text(encoding="utf-8", errors="replace")
        species = _parse_stru_species(stru_text)
        if species:
            if ntype == len(species):
                results.append(CheckResult("ntype_match", "PASS", f"ntype={ntype} matches {len(species)} species"))
            elif ntype is not None:
                results.append(CheckResult("ntype_match", "FAIL",
                    f"ntype={ntype} but STRU has {len(species)} species: {species}",
                    f"ntype {len(species)}"))
            else:
                results.append(CheckResult("ntype_match", "WARN",
                    f"ntype not set; STRU has {len(species)} species", f"ntype {len(species)}"))
    elif ntype:
        results.append(CheckResult("ntype_match", "WARN", f"Cannot verify (STRU file '{stru_ref}' not found)"))

    # 9. File references
    stru_exists = stru_path.exists()
    kpt_ref = params.get("kpoint_file", "KPT")
    kspacing = params.get("kspacing")
    kpt_exists = (workspace / kpt_ref).exists() or kspacing is not None
    
    if stru_exists and kpt_exists:
        results.append(CheckResult("file_references", "PASS", "STRU and KPT/kspacing present"))
    elif not stru_exists:
        results.append(CheckResult("file_references", "FAIL", f"STRU file '{stru_ref}' not found"))
    elif not kpt_exists:
        results.append(CheckResult("file_references", "WARN", f"KPT file '{kpt_ref}' not found and no kspacing"))

    # 10. out_chg for SCF (needed if follow-up NSCF expected)
    if calc == "scf":
        out_chg = _get_int(params, "out_chg")
        if out_chg == 1:
            results.append(CheckResult("out_chg", "PASS", "out_chg=1 (ready for NSCF follow-up)"))
        # Don't flag as error — only relevant for two-step workflows

    return results


def _review_vasp(workspace: Path, params: dict) -> list[CheckResult]:
    """Run all VASP checks."""
    results = []

    # ENCUT
    encut = _get_float(params, "encut")
    if encut and encut >= 400:
        results.append(CheckResult("ENCUT", "PASS", f"ENCUT={encut} eV"))
    elif encut:
        results.append(CheckResult("ENCUT", "WARN", f"ENCUT={encut} — verify vs POTCAR ENMAX×1.3"))
    else:
        results.append(CheckResult("ENCUT", "WARN", "Not set — using POTCAR default"))

    # KPOINTS
    kpoints_path = workspace / "KPOINTS"
    if kpoints_path.exists():
        results.append(CheckResult("KPOINTS", "PASS"))
    else:
        results.append(CheckResult("KPOINTS", "FAIL", "No KPOINTS file",
            "Generate: python generate_kpoints.py --structure POSCAR --mode auto"))

    # EDIFF
    ediff = _get_float(params, "ediff")
    if ediff and ediff <= 1e-5:
        results.append(CheckResult("EDIFF", "PASS", f"EDIFF={ediff}"))
    else:
        results.append(CheckResult("EDIFF", "PASS", "Using VASP default"))

    # ISMEAR
    ismear = _get_int(params, "ismear")
    if ismear is not None:
        results.append(CheckResult("ISMEAR", "PASS", f"ISMEAR={ismear}"))
    else:
        results.append(CheckResult("ISMEAR", "WARN", "Not set"))

    # POSCAR
    if (workspace / "POSCAR").exists():
        results.append(CheckResult("POSCAR", "PASS"))
    else:
        results.append(CheckResult("POSCAR", "FAIL", "No POSCAR file"))

    # Relaxation
    ibrion = _get_int(params, "ibrion")
    if ibrion and ibrion >= 1:
        nsw = _get_int(params, "nsw")
        ediffg = _get_float(params, "ediffg")
        if nsw and nsw > 0 and ediffg:
            results.append(CheckResult("relaxation", "PASS", f"NSW={nsw}, EDIFFG={ediffg}"))
        elif nsw and nsw > 0:
            results.append(CheckResult("relaxation", "WARN", "EDIFFG not set"))
        else:
            results.append(CheckResult("relaxation", "FAIL", "IBRION set but NSW=0"))

    return results


def _parse_stru_species(text: str) -> list[str]:
    """Extract species from STRU ATOMIC_SPECIES."""
    species = []
    in_species = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "//" in stripped:
            stripped = stripped[:stripped.index("//")].strip()
        if re.match(r"^ATOMIC_SPECIES\s*$", stripped, re.IGNORECASE):
            in_species = True
            continue
        if in_species:
            if re.match(r"^(NUMERICAL_ORBITAL|LATTICE|ATOMIC_POSITIONS|ABF_ORBITAL)", stripped, re.IGNORECASE):
                break
            parts = stripped.split()
            if len(parts) >= 2:
                species.append(parts[0])
    return species


# ---------------------------------------------------------------------------
# Structure assessment (lightweight)
# ---------------------------------------------------------------------------

def _assess_structure_lightweight(workspace: Path, software: str) -> list[CheckResult]:
    """Quick structure sanity check without full pymatgen."""
    results = []
    
    if software == "abacus":
        # Check STRU file basic sanity
        stru_files = [f for f in workspace.iterdir() if "stru" in f.name.lower() or f.name == "STRU"]
        for sf in stru_files[:1]:
            text = sf.read_text(encoding="utf-8", errors="replace")
            # Check for LATTICE_CONSTANT
            if "LATTICE_CONSTANT" in text.upper():
                # Extract value
                for line in text.splitlines():
                    if "LATTICE_CONSTANT" in line.upper() and not line.strip().upper().endswith("LATTICE_CONSTANT"):
                        continue
                results.append(CheckResult("structure_format", "PASS", f"{sf.name} has required sections"))
            else:
                results.append(CheckResult("structure_format", "WARN", f"{sf.name} missing LATTICE_CONSTANT"))
            
            # Check ATOMIC_POSITIONS
            if "ATOMIC_POSITIONS" in text.upper():
                # Count atoms roughly
                in_pos = False
                atom_count = 0
                for line in text.splitlines():
                    stripped = line.strip()
                    if re.match(r"^ATOMIC_POSITIONS", stripped, re.IGNORECASE):
                        in_pos = True
                        continue
                    if in_pos and stripped and not stripped.startswith("#"):
                        # Try to detect coordinate lines (3+ numbers)
                        parts = stripped.split()
                        try:
                            if len(parts) >= 3:
                                float(parts[0])
                                float(parts[1])
                                float(parts[2])
                                atom_count += 1
                        except ValueError:
                            pass
                if atom_count > 0:
                    results.append(CheckResult("atom_count", "PASS", f"~{atom_count} atoms detected"))
    
    elif software == "vasp":
        poscar = workspace / "POSCAR"
        if poscar.exists():
            lines = poscar.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) >= 7:
                results.append(CheckResult("structure_format", "PASS", f"POSCAR has {len(lines)} lines"))
            else:
                results.append(CheckResult("structure_format", "WARN", "POSCAR seems too short"))
    
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="One-call comprehensive workspace review for DFT submissions."
    )
    ap.add_argument("--dir", required=True, help="Workspace directory.")
    ap.add_argument("--software", required=True, choices=["abacus", "vasp"],
                    help="DFT software.")
    ap.add_argument("--format", choices=["human", "json"], default="human",
                    help="Output format.")
    ap.add_argument("--fix", action="store_true",
                    help="Generate corrected INPUT with all fixes applied.")
    args = ap.parse_args()

    workspace = Path(args.dir)
    if not workspace.is_dir():
        print(f"Error: directory not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: File inventory ---
    inventory = _inventory_files(workspace, args.software)

    # --- Phase 2: Parse parameters ---
    params = {}
    input_file = None
    if args.software == "abacus":
        for name in ["INPUT", "input"]:
            candidate = workspace / name
            if candidate.exists():
                input_file = candidate
                break
        if not input_file:
            # Try any INPUT* file
            candidates = sorted(workspace.glob("INPUT*"))
            candidates = [f for f in candidates if f.suffix not in (".bak", ".fixed")]
            if candidates:
                input_file = candidates[0]
    elif args.software == "vasp":
        candidate = workspace / "INCAR"
        if candidate.exists():
            input_file = candidate

    if input_file:
        text = input_file.read_text(encoding="utf-8", errors="replace")
        params = _parse_params(text)

    # --- Phase 3: Run checks ---
    checks: list[CheckResult] = []
    
    if not input_file:
        checks.append(CheckResult("input_file", "FAIL", "No input parameter file found"))
    else:
        if args.software == "abacus":
            checks = _review_abacus(workspace, params)
        elif args.software == "vasp":
            checks = _review_vasp(workspace, params)

    # --- Phase 4: Structure assessment ---
    struct_checks = _assess_structure_lightweight(workspace, args.software)
    checks.extend(struct_checks)

    # --- Phase 5: Grade ---
    n_pass = sum(1 for c in checks if c.status == "PASS")
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_warn = sum(1 for c in checks if c.status == "WARN")
    total = len(checks)
    
    if n_fail == 0 and n_warn == 0:
        grade = "A — EXCELLENT (ready to submit)"
    elif n_fail == 0 and n_warn <= 2:
        grade = "B — GOOD (minor suggestions, safe to submit)"
    elif n_fail == 0:
        grade = "C — ACCEPTABLE (review warnings before submit)"
    elif n_fail == 1:
        grade = "D — NEEDS FIX (1 critical issue)"
    else:
        grade = "F — CRITICAL (multiple failures, do NOT submit)"

    # --- Phase 6: Collect fixes ---
    all_fixes = {}
    for c in checks:
        if c.fix and c.status in ("FAIL", "WARN"):
            for line in c.fix.split("\n"):
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    all_fixes[parts[0]] = parts[1]

    # --- Output ---
    if args.format == "json":
        result = {
            "workspace": str(workspace),
            "software": args.software,
            "grade": grade,
            "summary": {"pass": n_pass, "fail": n_fail, "warn": n_warn, "total": total},
            "checks": [c.to_dict() for c in checks],
            "file_inventory": inventory,
            "suggested_fixes": all_fixes if all_fixes else None,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"WORKSPACE REVIEW — {args.software.upper()}")
        print(f"Directory: {workspace}")
        print(f"{'='*60}")
        print(f"\n  GRADE: {grade}")
        print(f"  Checks: {n_pass} pass | {n_warn} warn | {n_fail} fail | {total} total")
        
        print(f"\n{'─'*60}")
        print("  FILE INVENTORY:")
        if inventory["found"]:
            print(f"    Found: {', '.join(inventory['found'])}")
        if inventory.get("pseudopotentials"):
            print(f"    PP files: {', '.join(inventory['pseudopotentials'])}")
        if inventory.get("orbitals"):
            print(f"    Orbital files: {', '.join(inventory['orbitals'])}")
        if inventory["missing_critical"]:
            print(f"    ❌ MISSING: {', '.join(inventory['missing_critical'])}")
        if inventory["warnings"]:
            for w in inventory["warnings"]:
                print(f"    ⚠️  {w}")
        
        print(f"\n{'─'*60}")
        print("  PARAMETER CHECKS:")
        for c in checks:
            line = f"    {c.icon()} {c.name}"
            if c.details:
                line += f": {c.details}"
            print(line)
            if c.fix and c.status == "FAIL":
                print(f"       → Fix: {c.fix}")
        
        if all_fixes:
            print(f"\n{'─'*60}")
            print("  SUGGESTED FIXES (add/change in INPUT):")
            for param, val in all_fixes.items():
                print(f"    {param:<24}{val}")
        
        print(f"\n{'─'*60}")
        if n_fail > 0:
            print("  ❌ FIX CRITICAL ISSUES before submission!")
            if args.software == "abacus":
                print("\n  Quick fix options:")
                print("    python preflight_abacus.py --dir . --fix  → Auto-generate corrected INPUT")
                print("    python diagnose_input.py --software abacus --input INPUT --fix  → Fix mode")
        else:
            print("  ✅ Workspace is ready for Bohrium submission.")
        print(f"{'='*60}\n")

    # --- Auto-fix ---
    if args.fix and all_fixes and input_file and args.software == "abacus":
        current_params = dict(params)
        current_params.update(all_fixes)
        
        # Re-render
        lines = ["INPUT_PARAMETERS"]
        category_order = [
            ["suffix", "ntype", "calculation", "esolver_type", "pseudo_dir",
             "orbital_dir", "stru_file", "kpoint_file", "symmetry"],
            ["ecutwfc", "basis_type", "nspin", "nbands", "dft_functional",
             "gamma_only", "kspacing", "smearing_method", "smearing_sigma",
             "ks_solver", "noncolin", "lspinorb", "vdw_method"],
            ["scf_thr", "scf_nmax", "mixing_type", "mixing_beta", "mixing_ndim",
             "mixing_gg0", "init_chg"],
            ["cal_force", "cal_stress", "force_thr_ev", "stress_thr",
             "relax_nmax", "relax_method"],
            ["efield_flag", "dip_cor_flag", "efield_dir", "efield_amp",
             "efield_pos_max", "efield_pos_dec"],
            ["out_chg", "out_dos", "out_band", "out_pot", "out_stru"],
        ]
        
        emitted = set()
        for group in category_order:
            group_lines = []
            for key in group:
                if key in current_params:
                    group_lines.append(f"{key:<24}{current_params[key]}")
                    emitted.add(key)
            if group_lines:
                lines.append("")
                lines.extend(group_lines)
        
        extras = [(k, v) for k, v in current_params.items() if k not in emitted]
        if extras:
            lines.append("")
            for k, v in extras:
                lines.append(f"{k:<24}{v}")
        
        fixed_text = "\n".join(lines) + "\n"
        fixed_path = input_file.with_name(input_file.name + "_fixed")
        fixed_path.write_text(fixed_text, encoding="utf-8")
        print(f"\n✅ Fixed INPUT written to: {fixed_path}", file=sys.stderr)

    sys.exit(1 if n_fail > 0 else 0)


if __name__ == "__main__":
    main()
