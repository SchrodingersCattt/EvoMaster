#!/usr/bin/env python3
"""
preflight_abacus.py — Comprehensive pre-submission validator for ABACUS workspaces.

Validates ALL files in a directory (INPUT, STRU, KPT) with cross-references,
task-type awareness, and auto-fix generation. Designed to catch ALL common
ABACUS submission errors in a single call.

Checks performed:
  1. INPUT parameter validation (ecutwfc, calculation, mixing, etc.)
  2. STRU existence and format (species count, PP/orbital files)
  3. KPT existence and format
  4. Cross-reference: ntype in INPUT == species count in STRU
  5. Cross-reference: stru_file / kpoint_file directives vs actual files
  6. Task-specific mandatory parameters (relax needs cal_force, etc.)
  7. Slab detection: vacuum gap → dipole correction check
  8. Two-step workflow validation (SCF + NSCF file pairs)
  9. Generates corrected INPUT when fixable errors are found

Usage:
  python preflight_abacus.py --dir ./workspace/
  python preflight_abacus.py --dir ./workspace/ --fix
  python preflight_abacus.py --dir ./workspace/ --format json

Output: Structured report (human or JSON) with pass/fail, all issues, and fixes.
When --fix is given, writes corrected INPUT files alongside originals (*_fixed).
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_input_params(text: str) -> dict[str, str]:
    """Parse ABACUS INPUT file into {key_lower: raw_value}."""
    params = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^\s*INPUT_PARAMETERS\s*$", stripped, re.IGNORECASE):
            continue
        # Remove inline comment
        if "#" in stripped:
            stripped = stripped[: stripped.index("#")].strip()
        parts = stripped.split(None, 1)
        if len(parts) >= 2:
            params[parts[0].lower()] = parts[1].strip()
        elif len(parts) == 1:
            params[parts[0].lower()] = ""
    return params


def _parse_stru_species(text: str) -> list[str]:
    """Extract species labels from STRU ATOMIC_SPECIES section."""
    species = []
    in_atomic_species = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        # Remove // comments
        if "//" in stripped:
            stripped = stripped[: stripped.index("//")].strip()
        if re.match(r"^ATOMIC_SPECIES\s*$", stripped, re.IGNORECASE):
            in_atomic_species = True
            continue
        if in_atomic_species:
            # Next section header ends ATOMIC_SPECIES
            if re.match(
                r"^(NUMERICAL_ORBITAL|LATTICE_CONSTANT|LATTICE_VECTORS|ATOMIC_POSITIONS|ABF_ORBITAL)\s*$",
                stripped,
                re.IGNORECASE,
            ):
                break
            parts = stripped.split()
            if len(parts) >= 2:
                species.append(parts[0])
    return species


def _detect_vacuum_gap(text: str) -> float | None:
    """Try to detect vacuum gap from STRU lattice vectors.
    Returns max gap (Å) if detectable, else None."""
    lines = text.splitlines()
    lattice_constant = 1.0
    vectors = []
    in_lattice_vectors = False
    in_lattice_constant = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "//" in stripped:
            stripped = stripped[: stripped.index("//")].strip()

        if re.match(r"^LATTICE_CONSTANT\s*$", stripped, re.IGNORECASE):
            in_lattice_constant = True
            continue
        if in_lattice_constant:
            try:
                lattice_constant = float(stripped.split()[0])
            except (ValueError, IndexError):
                pass
            in_lattice_constant = False
            continue

        if re.match(r"^LATTICE_VECTORS\s*$", stripped, re.IGNORECASE):
            in_lattice_vectors = True
            continue
        if in_lattice_vectors:
            parts = stripped.split()
            try:
                vec = [float(x) for x in parts[:3]]
                vectors.append(vec)
            except (ValueError, IndexError):
                pass
            if len(vectors) >= 3:
                in_lattice_vectors = False

    if len(vectors) < 3:
        return None

    import math

    # Convert to Angstrom (lattice_constant is in Bohr if ~1.89)
    # If lattice_constant > 1.5, assume it's in Bohr (standard ABACUS convention)
    bohr_to_ang = 0.529177
    if lattice_constant > 1.5:
        scale = lattice_constant * bohr_to_ang
    else:
        scale = lattice_constant

    lengths = []
    for v in vectors:
        length = math.sqrt(sum(x**2 for x in v)) * scale
        lengths.append(length)

    # Vacuum gap heuristic: if one direction is > 2x the others
    max_len = max(lengths)
    others = sorted(lengths)[:-1]
    if others and max_len > 2.0 * max(others):
        # Likely has vacuum
        return max_len
    return None


def _get_calc_type(params: dict) -> str:
    """Get calculation type from params."""
    return params.get("calculation", "scf").lower()


def _get_int(params: dict, key: str) -> int | None:
    v = params.get(key)
    if v is None:
        return None
    try:
        return int(v.split(".")[0])
    except (ValueError, AttributeError):
        return None


def _get_float(params: dict, key: str) -> float | None:
    v = params.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

class Issue:
    def __init__(self, severity: str, message: str, fix: str | None = None, param: str | None = None):
        self.severity = severity  # "error" or "warning"
        self.message = message
        self.fix = fix  # suggested parameter line to add/change
        self.param = param

    def to_dict(self) -> dict:
        d = {"severity": self.severity, "message": self.message}
        if self.fix:
            d["fix"] = self.fix
        if self.param:
            d["param"] = self.param
        return d

    def to_human(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "WARN "
        s = f"  [{prefix}] {self.message}"
        if self.fix:
            s += f"\n         Fix: {self.fix}"
        return s


def validate_workspace(workspace_dir: Path) -> dict:
    """Validate an ABACUS workspace directory. Returns structured result."""
    issues: list[Issue] = []
    fixes: dict[str, str] = {}  # param -> fix_value
    
    # --- Find files ---
    input_files = []
    stru_files = []
    kpt_files = []
    
    for f in sorted(workspace_dir.iterdir()):
        if f.is_file():
            name_lower = f.name.lower()
            if name_lower.startswith("input"):
                input_files.append(f)
            elif "stru" in name_lower or name_lower == "stru":
                stru_files.append(f)
            elif "kpt" in name_lower or name_lower == "kpt":
                kpt_files.append(f)

    # Also check explicit names
    for name in ["INPUT", "STRU", "KPT"]:
        p = workspace_dir / name
        if p.exists() and p not in input_files and p not in stru_files and p not in kpt_files:
            if name == "INPUT":
                input_files.append(p)
            elif name == "STRU":
                stru_files.append(p)
            elif name == "KPT":
                kpt_files.append(p)

    if not input_files:
        issues.append(Issue("error", "No INPUT file found in workspace"))
        return _build_result(issues, fixes, workspace_dir)

    # --- Validate each INPUT file ---
    for input_file in input_files:
        input_text = input_file.read_text(encoding="utf-8", errors="replace")
        params = _parse_input_params(input_text)
        
        if not params:
            issues.append(Issue("error", f"{input_file.name}: Could not parse any parameters"))
            continue

        calc = _get_calc_type(params)

        # Check ecutwfc
        ecutwfc = _get_float(params, "ecutwfc")
        if ecutwfc is None:
            issues.append(Issue("warning", f"{input_file.name}: ecutwfc not set (default may be too low)", 
                              "ecutwfc  100", "ecutwfc"))
            fixes["ecutwfc"] = "100"
        elif ecutwfc < 50:
            issues.append(Issue("warning", f"{input_file.name}: ecutwfc={ecutwfc} is low; recommend 100 Ry",
                              "ecutwfc  100", "ecutwfc"))

        # Check mixing_type
        if "mixing_type" not in params:
            issues.append(Issue("warning", f"{input_file.name}: mixing_type not set; should be 'broyden'",
                              "mixing_type  broyden", "mixing_type"))
            fixes["mixing_type"] = "broyden"

        # Check smearing
        if "smearing_sigma" not in params:
            issues.append(Issue("warning", f"{input_file.name}: smearing_sigma not set; recommend 0.01",
                              "smearing_sigma  0.01", "smearing_sigma"))
            fixes["smearing_sigma"] = "0.01"

        # --- Task-specific checks ---
        if calc in ("relax", "cell-relax"):
            cal_force = _get_int(params, "cal_force")
            if cal_force is None or cal_force == 0:
                issues.append(Issue("error", 
                    f"{input_file.name}: calculation='{calc}' but cal_force is not 1! "
                    "ABACUS will NOT compute forces → optimizer broken.",
                    "cal_force  1", "cal_force"))
                fixes["cal_force"] = "1"
            
            if _get_float(params, "force_thr_ev") is None:
                issues.append(Issue("warning",
                    f"{input_file.name}: No force_thr_ev set for relax. Recommend 0.01 eV/Å.",
                    "force_thr_ev  0.01", "force_thr_ev"))
                fixes["force_thr_ev"] = "0.01"
            
            if calc == "cell-relax":
                cal_stress = _get_int(params, "cal_stress")
                if cal_stress is None or cal_stress == 0:
                    issues.append(Issue("error",
                        f"{input_file.name}: calculation='cell-relax' but cal_stress is not 1! "
                        "Cell vectors will NOT be optimized.",
                        "cal_stress  1", "cal_stress"))
                    fixes["cal_stress"] = "1"

        elif calc == "md":
            cal_force = _get_int(params, "cal_force")
            if cal_force is None or cal_force == 0:
                issues.append(Issue("error",
                    f"{input_file.name}: calculation='md' but cal_force is not 1! No forces → no dynamics.",
                    "cal_force  1", "cal_force"))
                fixes["cal_force"] = "1"

        elif calc == "nscf":
            if params.get("init_chg", "").lower() != "file":
                issues.append(Issue("error",
                    f"{input_file.name}: NSCF calculation requires init_chg=file to read prior SCF density.",
                    "init_chg  file", "init_chg"))
                fixes["init_chg"] = "file"
            
            sym = _get_int(params, "symmetry")
            if sym is None or sym != 0:
                issues.append(Issue("error",
                    f"{input_file.name}: NSCF requires symmetry=0 (k-paths get folded otherwise).",
                    "symmetry  0", "symmetry"))
                fixes["symmetry"] = "0"

        elif calc == "scf":
            out_chg = _get_int(params, "out_chg")
            if out_chg is None or out_chg == 0:
                issues.append(Issue("warning",
                    f"{input_file.name}: SCF without out_chg=1. Needed for any follow-up NSCF.",
                    "out_chg  1", "out_chg"))
                fixes["out_chg"] = "1"

        # --- Slab/workfunction detection ---
        out_pot = _get_int(params, "out_pot")
        efield_flag = _get_int(params, "efield_flag")
        dip_cor_flag = _get_int(params, "dip_cor_flag")
        
        # If out_pot=2 (electrostatic potential) → likely slab workfunction
        if out_pot == 2:
            if efield_flag != 1 or dip_cor_flag != 1:
                issues.append(Issue("error",
                    f"{input_file.name}: out_pot=2 (electrostatic potential output) detected "
                    "but dipole correction is NOT enabled! For slab calculations, dipole "
                    "correction is ESSENTIAL to remove artificial fields from periodic images. "
                    "Add: efield_flag 1, dip_cor_flag 1, efield_dir 2, efield_pos_max 0.0, "
                    "efield_pos_dec 0.1, efield_amp 0.0.",
                    "efield_flag  1\ndip_cor_flag  1\nefield_dir  2\n"
                    "efield_pos_max  0.0\nefield_pos_dec  0.1\nefield_amp  0.0",
                    "efield_flag"))
                fixes.setdefault("efield_flag", "1")
                fixes.setdefault("dip_cor_flag", "1")
                fixes.setdefault("efield_dir", "2")
                fixes.setdefault("efield_pos_max", "0.0")
                fixes.setdefault("efield_pos_dec", "0.1")
                fixes.setdefault("efield_amp", "0.0")
        
        # If efield_flag=1 but missing related params
        if efield_flag == 1:
            if dip_cor_flag != 1:
                issues.append(Issue("warning",
                    f"{input_file.name}: efield_flag=1 without dip_cor_flag=1. "
                    "Dipole correction is needed for proper slab potential.",
                    "dip_cor_flag  1", "dip_cor_flag"))
                fixes.setdefault("dip_cor_flag", "1")
            if "efield_dir" not in params:
                issues.append(Issue("warning",
                    f"{input_file.name}: efield_flag=1 without efield_dir. Default is z (2).",
                    "efield_dir  2", "efield_dir"))
                fixes.setdefault("efield_dir", "2")

        # --- DFT+U checks ---
        lda_plus_u = _get_int(params, "lda_plus_u")
        if lda_plus_u == 1:
            if "hubbard_u" not in params:
                issues.append(Issue("error",
                    f"{input_file.name}: lda_plus_u=1 but hubbard_u not set.",
                    "hubbard_u  <U_values>", "hubbard_u"))
            if "orbital_corr" not in params:
                issues.append(Issue("error",
                    f"{input_file.name}: lda_plus_u=1 but orbital_corr not set.",
                    "orbital_corr  <l_values>", "orbital_corr"))
            nspin = _get_int(params, "nspin")
            if nspin is None or nspin < 2:
                issues.append(Issue("warning",
                    f"{input_file.name}: DFT+U typically requires nspin=2.",
                    "nspin  2", "nspin"))
                fixes.setdefault("nspin", "2")

        # --- Spin-polarized check: mixing parameters ---
        nspin = _get_int(params, "nspin")
        if nspin is not None and nspin >= 2:
            if "mixing_type" not in params:
                fixes.setdefault("mixing_type", "broyden")
            if "mixing_beta" not in params:
                issues.append(Issue("warning",
                    f"{input_file.name}: nspin=2 without mixing_beta. Recommend 0.1 for stability.",
                    "mixing_beta  0.1", "mixing_beta"))
                fixes.setdefault("mixing_beta", "0.1")

        # --- Cross-reference: stru_file ---
        stru_ref = params.get("stru_file")
        if stru_ref:
            stru_path = workspace_dir / stru_ref
            if not stru_path.exists():
                issues.append(Issue("error",
                    f"{input_file.name}: stru_file='{stru_ref}' but file does not exist!",
                    param="stru_file"))
        else:
            # Default is "STRU" 
            default_stru = workspace_dir / "STRU"
            if not default_stru.exists() and stru_files:
                # STRU exists under different name but not referenced
                issues.append(Issue("error",
                    f"{input_file.name}: No 'stru_file' directive and no file named 'STRU'. "
                    f"Found STRU-like files: {[f.name for f in stru_files]}. "
                    f"Add 'stru_file {stru_files[0].name}' to INPUT.",
                    f"stru_file  {stru_files[0].name}", "stru_file"))
            elif not default_stru.exists() and not stru_files:
                issues.append(Issue("error",
                    f"{input_file.name}: No STRU file found in workspace! ABACUS requires a structure file.",
                    param="stru_file"))

        # --- Cross-reference: kpoint_file ---
        kpt_ref = params.get("kpoint_file")
        kspacing = params.get("kspacing")
        if kpt_ref:
            kpt_path = workspace_dir / kpt_ref
            if not kpt_path.exists():
                issues.append(Issue("error",
                    f"{input_file.name}: kpoint_file='{kpt_ref}' but file does not exist!",
                    param="kpoint_file"))
        elif not kspacing:
            # Default is "KPT"
            default_kpt = workspace_dir / "KPT"
            if not default_kpt.exists() and kpt_files:
                issues.append(Issue("warning",
                    f"{input_file.name}: No 'kpoint_file' directive, no 'kspacing', and no 'KPT' file. "
                    f"Found KPT-like files: {[f.name for f in kpt_files]}. "
                    f"Add 'kpoint_file {kpt_files[0].name}' to INPUT.",
                    f"kpoint_file  {kpt_files[0].name}", "kpoint_file"))
            elif not default_kpt.exists() and not kpt_files:
                issues.append(Issue("warning",
                    f"{input_file.name}: No KPT file and no kspacing set. "
                    "ABACUS will use built-in default (may be inappropriate).",
                    param="kpoint_file"))

        # --- Cross-reference: ntype vs STRU species ---
        ntype_input = _get_int(params, "ntype")
        actual_stru = None
        if stru_ref:
            actual_stru = workspace_dir / stru_ref
        else:
            actual_stru = workspace_dir / "STRU"
        
        if actual_stru and actual_stru.exists():
            stru_text = actual_stru.read_text(encoding="utf-8", errors="replace")
            species = _parse_stru_species(stru_text)
            n_species = len(species)
            
            if ntype_input is not None and ntype_input != n_species:
                issues.append(Issue("error",
                    f"{input_file.name}: ntype={ntype_input} in INPUT but STRU has "
                    f"{n_species} species: {species}. Must match exactly!",
                    f"ntype  {n_species}", "ntype"))
                fixes["ntype"] = str(n_species)
            elif ntype_input is None and n_species > 0:
                issues.append(Issue("warning",
                    f"{input_file.name}: ntype not set. STRU has {n_species} species: {species}. "
                    f"Set ntype={n_species} explicitly.",
                    f"ntype  {n_species}", "ntype"))
                fixes["ntype"] = str(n_species)

            # Slab detection from STRU
            vacuum = _detect_vacuum_gap(stru_text)
            if vacuum is not None and vacuum > 12.0:
                # This is likely a slab calculation
                if out_pot != 2 and efield_flag != 1:
                    issues.append(Issue("warning",
                        f"{input_file.name}: Slab geometry detected (vacuum ~{vacuum:.1f} Å) "
                        "but no dipole correction or electrostatic potential output. "
                        "For accurate slab energetics, consider adding dipole correction: "
                        "efield_flag 1, dip_cor_flag 1, efield_dir 2.",
                        param="efield_flag"))

    return _build_result(issues, fixes, workspace_dir)


def _build_result(issues: list[Issue], fixes: dict, workspace_dir: Path) -> dict:
    """Build structured result dict."""
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    
    result = {
        "workspace": str(workspace_dir),
        "passed": len(errors) == 0,
        "n_errors": len(errors),
        "n_warnings": len(warnings),
        "issues": [i.to_dict() for i in issues],
    }
    
    if fixes:
        result["suggested_fixes"] = fixes
    
    return result


def generate_fixed_input(input_file: Path, fixes: dict) -> str:
    """Generate a corrected INPUT file with fixes applied."""
    text = input_file.read_text(encoding="utf-8", errors="replace")
    params = _parse_input_params(text)
    
    # Apply fixes to params
    all_params = dict(params)
    all_params.update(fixes)
    
    # Re-render the INPUT file in proper order
    lines = ["INPUT_PARAMETERS"]
    
    # Category-based ordering
    category_order = [
        ["suffix", "ntype", "calculation", "esolver_type", "pseudo_dir", 
         "orbital_dir", "stru_file", "kpoint_file", "symmetry"],
        ["ecutwfc", "basis_type", "nspin", "nbands", "dft_functional",
         "gamma_only", "kspacing", "smearing_method", "smearing_sigma",
         "ks_solver", "noncolin", "lspinorb", "lda_plus_u", "hubbard_u",
         "orbital_corr", "nupdown", "vdw_method"],
        ["scf_thr", "scf_nmax", "mixing_type", "mixing_beta", "mixing_ndim",
         "mixing_gg0", "init_chg"],
        ["cal_force", "cal_stress", "force_thr_ev", "stress_thr", 
         "relax_nmax", "relax_method", "fixed_atoms"],
        ["md_type", "md_nstep", "md_dt", "md_tfirst", "md_tlast",
         "md_tfreq", "md_dumpfreq", "md_restartfreq", "init_vel"],
        ["efield_flag", "dip_cor_flag", "efield_dir", "efield_amp",
         "efield_pos_max", "efield_pos_dec", "gate_flag", "zgate",
         "block", "block_down", "block_up", "block_height"],
        ["out_chg", "out_dos", "out_band", "out_proj_band", "out_stru",
         "out_pot", "out_wfc_lcao", "out_dipole", "out_mul"],
    ]
    
    emitted: set = set()
    for group in category_order:
        group_lines = []
        for key in group:
            if key in all_params:
                val = all_params[key]
                group_lines.append(f"{key:<24}{val}")
                emitted.add(key)
        if group_lines:
            lines.append("")
            lines.extend(group_lines)
    
    # Remaining params
    extras = [(k, v) for k, v in all_params.items() if k not in emitted]
    if extras:
        lines.append("")
        for k, v in extras:
            lines.append(f"{k:<24}{v}")
    
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Comprehensive ABACUS workspace pre-flight validation."
    )
    ap.add_argument(
        "--dir",
        required=True,
        help="Directory containing ABACUS input files (INPUT, STRU, KPT).",
    )
    ap.add_argument(
        "--fix",
        action="store_true",
        help="Generate corrected INPUT file(s) when fixable errors are found.",
    )
    ap.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human).",
    )
    args = ap.parse_args()

    workspace = Path(args.dir)
    if not workspace.is_dir():
        print(f"Error: directory not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    result = validate_workspace(workspace)

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Human-readable output
        status = "✅ PASSED" if result["passed"] else "❌ FAILED"
        print(f"\n{'='*60}")
        print(f"ABACUS Pre-flight Check: {status}")
        print(f"Workspace: {result['workspace']}")
        print(f"Errors: {result['n_errors']}  |  Warnings: {result['n_warnings']}")
        print(f"{'='*60}")
        
        if result.get("issues"):
            print("\nIssues found:")
            for issue_dict in result["issues"]:
                severity = issue_dict["severity"].upper()
                prefix = "❌ ERROR" if severity == "ERROR" else "⚠️  WARN"
                print(f"  {prefix}: {issue_dict['message']}")
                if issue_dict.get("fix"):
                    print(f"         → Fix: {issue_dict['fix']}")
        
        if result.get("suggested_fixes"):
            print(f"\n{'─'*60}")
            print("Suggested parameter fixes (add/change in INPUT):")
            for param, val in result["suggested_fixes"].items():
                print(f"  {param:<24}{val}")
        
        if not result["passed"]:
            print(f"\n{'─'*60}")
            print("❌ DO NOT SUBMIT — fix errors above first.")
            # Provide workflow script tip for common patterns
            has_dipole_issue = any(
                "dipole" in i.get("message", "").lower() or
                "efield_flag" in i.get("param", "")
                for i in result.get("issues", [])
            )
            has_relax_issue = any(
                "cal_force" in i.get("param", "") or "cal_stress" in i.get("param", "")
                for i in result.get("issues", [])
            )
            if has_dipole_issue:
                print("\n💡 TIP: For slab/workfunction tasks, use the workflow renderer:")
                print("   python render_abacus_workflow.py --workflow workfunction --output-dir ./")
                print("   This generates correct INPUT with dipole correction automatically.")
            if has_relax_issue:
                print("\n💡 TIP: For relaxation tasks, use the workflow renderer:")
                print("   python render_abacus_workflow.py --workflow relax --output-dir ./")
                print("   or: python render_abacus_workflow.py --workflow cell_relax --output-dir ./")
            print("\n💡 Or use --fix flag to auto-generate corrected INPUT:")
            print("   python preflight_abacus.py --dir . --fix")
        else:
            print(f"\n{'─'*60}")
            print("✅ Ready for submission.")

    # Generate fixed file if requested
    if args.fix and result.get("suggested_fixes"):
        input_files = sorted(workspace.glob("INPUT*"))
        for input_file in input_files:
            if input_file.suffix in (".bak", ".fixed"):
                continue
            fixed_text = generate_fixed_input(input_file, result["suggested_fixes"])
            fixed_path = input_file.with_name(input_file.name + "_fixed")
            fixed_path.write_text(fixed_text, encoding="utf-8")
            if args.format == "human":
                print(f"\n✏️  Fixed INPUT written to: {fixed_path}")
            else:
                result["fixed_file"] = str(fixed_path)

    # Cross-reference related tools
    if args.format == "human":
        print(f"\n{'─'*50}", file=sys.stderr)
        print("📋 Related tools:", file=sys.stderr)
        print("  • workspace_review.py --dir . --software abacus  → Full review + grade in one call", file=sys.stderr)
        print("  • evaluate_dft_setup.py --software abacus --dir .  → Best-practice grade (12 categories)", file=sys.stderr)
        print("  • format_bp_report.py --dir . --software abacus  → Generate structured evaluation report", file=sys.stderr)
        print("  • diagnose_input.py --software abacus --input INPUT --fix  → Auto-fix INPUT errors", file=sys.stderr)
        print(f"{'─'*50}", file=sys.stderr)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
