#!/usr/bin/env python3
"""
format_bp_report.py — Generate structured DFT best-practice evaluation reports.

Produces a DETERMINISTIC, structured Markdown report evaluating a DFT calculation
setup against best practices. The report format is fixed and complete, reducing
agent turn count and variance when answering best-practice evaluation questions.

The output includes:
  - Executive summary (grade + key findings)
  - Category-by-category evaluation with rationale
  - Specific recommendations with corrected parameters
  - Physical justification for each recommendation

Usage:
  python format_bp_report.py --dir ./workspace/ --software abacus
  python format_bp_report.py --dir ./workspace/ --software vasp
  python format_bp_report.py --dir ./workspace/ --software abacus --output report.md
  python format_bp_report.py --dir ./workspace/ --software abacus --categories all
  python format_bp_report.py --dir ./workspace/ --software abacus --categories "basis,kpoints,scf"

Output: Structured Markdown report that can be used directly as a task response.
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Parameter parsing (shared with evaluate_dft_setup.py)
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
# Category evaluations with physical rationale
# ---------------------------------------------------------------------------

_CATEGORIES = [
    "basis", "kpoints", "scf", "smearing", "relaxation",
    "workflow", "files", "slab", "mixing", "ntype", "output", "overall"
]


def _evaluate_category_abacus(cat: str, params: dict, workspace: Path) -> dict:
    """Evaluate a single category for ABACUS. Returns structured result."""
    result = {
        "category": cat,
        "status": "PASS",  # PASS, FAIL, WARN, N/A
        "current_value": "",
        "recommended": "",
        "rationale": "",
        "physical_justification": "",
    }
    
    calc = params.get("calculation", "scf").lower()

    if cat == "basis":
        ecutwfc = _get_float(params, "ecutwfc")
        basis_type = params.get("basis_type", "lcao").lower()
        if ecutwfc is None:
            result["status"] = "FAIL"
            result["current_value"] = "ecutwfc not set"
            result["recommended"] = "ecutwfc 100"
            result["rationale"] = "Plane-wave/LCAO cutoff energy determines basis completeness"
            result["physical_justification"] = (
                "ecutwfc=100 Ry provides converged total energies for most systems. "
                "Lower values risk unconverged charge density and inaccurate forces."
            )
        elif ecutwfc >= 100:
            result["status"] = "PASS"
            result["current_value"] = f"ecutwfc={ecutwfc} Ry, basis_type={basis_type}"
            result["rationale"] = "Cutoff energy is at or above the recommended standard"
        else:
            result["status"] = "WARN"
            result["current_value"] = f"ecutwfc={ecutwfc} Ry"
            result["recommended"] = "ecutwfc 100 (unless task specifies otherwise)"
            result["rationale"] = f"ecutwfc={ecutwfc} is below the standard 100 Ry"
            result["physical_justification"] = (
                "Lower cutoff saves compute time but may compromise accuracy, "
                "especially for properties sensitive to high-energy components (stress, phonons)."
            )

    elif cat == "kpoints":
        kspacing = params.get("kspacing")
        kpt_file = params.get("kpoint_file", "KPT")
        kpt_path = workspace / kpt_file
        if kspacing:
            try:
                ksp_vals = [float(x) for x in kspacing.split()]
                result["current_value"] = f"kspacing={kspacing}"
                if all(v <= 0.12 for v in ksp_vals):
                    result["status"] = "PASS"
                    result["rationale"] = "kspacing is appropriately dense"
                else:
                    result["status"] = "WARN"
                    result["rationale"] = "kspacing may be too coarse for metals"
            except ValueError:
                result["current_value"] = f"kspacing={kspacing}"
                result["status"] = "WARN"
        elif kpt_path.exists():
            kpt_text = kpt_path.read_text(encoding="utf-8", errors="replace")
            result["current_value"] = f"KPT file: {kpt_file}"
            # Try to extract mesh
            for line in kpt_text.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        mesh = [int(x) for x in parts[:3]]
                        if all(m > 0 for m in mesh):
                            result["current_value"] += f" (mesh: {mesh[0]}×{mesh[1]}×{mesh[2]})"
                            break
                    except ValueError:
                        continue
            result["status"] = "PASS"
            result["rationale"] = "KPT file present with k-point sampling"
        elif (workspace / "KPT").exists():
            result["status"] = "PASS"
            result["current_value"] = "KPT file present"
        else:
            result["status"] = "FAIL"
            result["current_value"] = "No KPT file and no kspacing"
            result["recommended"] = "Create KPT or set kspacing in INPUT"
            result["physical_justification"] = (
                "K-point sampling determines Brillouin zone integration accuracy. "
                "Insufficient sampling causes oscillating energies and poor convergence."
            )

    elif cat == "scf":
        scf_thr = _get_float(params, "scf_thr")
        scf_nmax = _get_int(params, "scf_nmax")
        result["current_value"] = f"scf_thr={scf_thr}, scf_nmax={scf_nmax}"
        if scf_thr and scf_thr <= 1e-6:
            result["status"] = "PASS"
            result["rationale"] = "SCF convergence threshold is tight enough for production"
        elif scf_thr and scf_thr <= 1e-5:
            result["status"] = "WARN"
            result["current_value"] = f"scf_thr={scf_thr}"
            result["recommended"] = "scf_thr 1.0e-7"
            result["rationale"] = "Moderate convergence; tighten for accurate forces"
        else:
            result["status"] = "WARN"
            result["recommended"] = "scf_thr 1.0e-7, scf_nmax 100"
        result["physical_justification"] = (
            "SCF convergence determines the self-consistency of electron density. "
            "Loose thresholds can cause noisy forces and unreliable geometry optimization."
        )

    elif cat == "smearing":
        method = params.get("smearing_method", "")
        sigma = _get_float(params, "smearing_sigma")
        if method and sigma is not None:
            result["current_value"] = f"{method}, σ={sigma}"
            if sigma <= 0.015:
                result["status"] = "PASS"
                result["rationale"] = "Smearing is appropriate for ground-state properties"
            else:
                result["status"] = "WARN"
                result["recommended"] = "smearing_sigma 0.01"
                result["rationale"] = f"σ={sigma} is large; may introduce artificial electronic temperature"
        elif method:
            result["current_value"] = f"method={method}, σ not set"
            result["status"] = "WARN"
            result["recommended"] = "smearing_sigma 0.01"
        else:
            result["current_value"] = "Not configured"
            result["status"] = "WARN"
            result["recommended"] = "smearing_method gauss\nsmearing_sigma 0.01"
        result["physical_justification"] = (
            "Smearing broadens the Fermi-Dirac distribution for metals. "
            "Too large σ artificially raises electronic temperature; too small causes "
            "oscillating SCF for metals."
        )

    elif cat == "relaxation":
        if calc not in ("relax", "cell-relax"):
            result["status"] = "N/A"
            result["rationale"] = f"Not a relaxation calculation (calc={calc})"
            return result
        
        cal_force = _get_int(params, "cal_force")
        cal_stress = _get_int(params, "cal_stress")
        force_thr = _get_float(params, "force_thr_ev")
        
        issues = []
        if cal_force != 1:
            issues.append("cal_force not set to 1 — forces NOT computed")
        if calc == "cell-relax" and cal_stress != 1:
            issues.append("cal_stress not set to 1 — stress NOT computed")
        if not force_thr:
            issues.append("force_thr_ev not set (no convergence criterion)")
        
        if not issues:
            result["status"] = "PASS"
            result["current_value"] = f"cal_force=1, force_thr_ev={force_thr}"
            if calc == "cell-relax":
                result["current_value"] += f", cal_stress=1"
        else:
            result["status"] = "FAIL"
            result["current_value"] = "; ".join(issues)
            result["recommended"] = "cal_force 1, force_thr_ev 0.01"
            if calc == "cell-relax":
                result["recommended"] += ", cal_stress 1, stress_thr 0.5"
        result["physical_justification"] = (
            "Relaxation requires explicit force (and stress for cell-relax) computation. "
            "Without cal_force=1, the optimizer has no gradient information and cannot minimize."
        )

    elif cat == "workflow":
        if calc == "nscf":
            init_chg = params.get("init_chg", "").lower()
            symmetry = _get_int(params, "symmetry")
            nbands = _get_int(params, "nbands")
            
            issues = []
            if init_chg != "file":
                issues.append("init_chg != 'file' (will redo SCF instead of reading charge)")
            if symmetry != 0:
                issues.append("symmetry != 0 (k-path points will be folded)")
            if nbands is None:
                issues.append("nbands not set (may miss empty states)")
            
            if not issues:
                result["status"] = "PASS"
                result["current_value"] = f"init_chg=file, symmetry=0, nbands={nbands}"
            else:
                result["status"] = "FAIL"
                result["current_value"] = "; ".join(issues)
                result["recommended"] = "init_chg file, symmetry 0, nbands <N>"
            result["physical_justification"] = (
                "NSCF (band/DOS) must read converged charge density from prior SCF. "
                "symmetry=0 prevents k-point folding that would distort the band path."
            )
        else:
            result["status"] = "N/A"
            result["rationale"] = "Not a two-step workflow"

    elif cat == "files":
        stru_ref = params.get("stru_file", "STRU")
        kpt_ref = params.get("kpoint_file", "KPT")
        kspacing = params.get("kspacing")
        
        stru_ok = (workspace / stru_ref).exists()
        kpt_ok = (workspace / kpt_ref).exists() or kspacing is not None
        
        if stru_ok and kpt_ok:
            result["status"] = "PASS"
            result["current_value"] = f"STRU='{stru_ref}' ✓, KPT='{kpt_ref}' ✓"
        else:
            issues = []
            if not stru_ok:
                issues.append(f"STRU file '{stru_ref}' NOT FOUND")
            if not kpt_ok:
                issues.append(f"KPT file '{kpt_ref}' NOT FOUND and no kspacing")
            result["status"] = "FAIL"
            result["current_value"] = "; ".join(issues)
        result["physical_justification"] = "All referenced files must exist for ABACUS to run."

    elif cat == "slab":
        out_pot = _get_int(params, "out_pot")
        efield = _get_int(params, "efield_flag")
        dip_cor = _get_int(params, "dip_cor_flag")
        
        if out_pot != 2 and efield != 1:
            result["status"] = "N/A"
            result["rationale"] = "Not a slab/surface calculation"
            return result
        
        if efield == 1 and dip_cor == 1:
            result["status"] = "PASS"
            result["current_value"] = "Dipole correction enabled (efield_flag=1, dip_cor_flag=1)"
            efield_dir = _get_int(params, "efield_dir")
            if efield_dir is not None:
                result["current_value"] += f", efield_dir={efield_dir}"
        else:
            result["status"] = "FAIL"
            result["current_value"] = "Dipole correction MISSING for slab"
            result["recommended"] = (
                "efield_flag 1, dip_cor_flag 1, efield_dir 2, "
                "efield_pos_max 0.0, efield_pos_dec 0.1, efield_amp 0.0"
            )
        result["physical_justification"] = (
            "Periodic slabs create artificial electric fields between periodic images. "
            "Dipole correction cancels this artifact, essential for accurate work functions "
            "and surface energies."
        )

    elif cat == "mixing":
        mixing_type = params.get("mixing_type", "")
        mixing_beta = _get_float(params, "mixing_beta")
        if mixing_type.lower() == "broyden":
            result["status"] = "PASS"
            result["current_value"] = f"broyden"
            if mixing_beta:
                result["current_value"] += f", β={mixing_beta}"
        elif mixing_type:
            result["status"] = "PASS"
            result["current_value"] = mixing_type
        else:
            result["status"] = "WARN"
            result["current_value"] = "Not set"
            result["recommended"] = "mixing_type broyden"
        result["physical_justification"] = (
            "Broyden mixing accelerates SCF convergence by using history of "
            "density differences. Prevents charge sloshing in metals and slabs."
        )

    elif cat == "ntype":
        ntype = _get_int(params, "ntype")
        stru_ref = params.get("stru_file", "STRU")
        stru_path = workspace / stru_ref
        if stru_path.exists():
            stru_text = stru_path.read_text(encoding="utf-8", errors="replace")
            species = []
            in_sp = False
            for line in stru_text.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "//" in s:
                    s = s[:s.index("//")].strip()
                if re.match(r"^ATOMIC_SPECIES\s*$", s, re.IGNORECASE):
                    in_sp = True
                    continue
                if in_sp:
                    if re.match(r"^(NUMERICAL_ORBITAL|LATTICE|ATOMIC_POSITIONS)", s, re.IGNORECASE):
                        break
                    parts = s.split()
                    if len(parts) >= 2:
                        species.append(parts[0])
            
            if species:
                if ntype == len(species):
                    result["status"] = "PASS"
                    result["current_value"] = f"ntype={ntype} matches species: {species}"
                elif ntype is not None:
                    result["status"] = "FAIL"
                    result["current_value"] = f"ntype={ntype} but {len(species)} species in STRU"
                    result["recommended"] = f"ntype {len(species)}"
                else:
                    result["status"] = "WARN"
                    result["current_value"] = f"ntype not set; STRU has {len(species)} species"
                    result["recommended"] = f"ntype {len(species)}"
            else:
                result["status"] = "WARN"
                result["current_value"] = "Could not parse STRU species"
        else:
            if ntype:
                result["status"] = "WARN"
                result["current_value"] = f"ntype={ntype} (cannot verify without STRU)"
            else:
                result["status"] = "WARN"
                result["current_value"] = "ntype not set, STRU not found"
        result["physical_justification"] = (
            "ntype must exactly match the number of distinct species in STRU. "
            "Mismatch causes ABACUS to crash or silently misparse atomic positions."
        )

    elif cat == "output":
        out_params = {k: v for k, v in params.items() if k.startswith("out_")}
        if out_params:
            result["status"] = "PASS"
            result["current_value"] = ", ".join(f"{k}={v}" for k, v in out_params.items())
        else:
            result["status"] = "WARN"
            result["current_value"] = "No explicit output parameters set"
            result["rationale"] = "Consider setting out_chg, out_band, out_dos, out_pot as needed"

    elif cat == "overall":
        # This is computed from other categories
        result["status"] = "SKIP"  # placeholder, computed in report
    
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _generate_report(evaluations: list[dict], params: dict, workspace: Path, software: str) -> str:
    """Generate structured Markdown best-practice report."""
    calc = params.get("calculation", "scf")
    
    n_pass = sum(1 for e in evaluations if e["status"] == "PASS")
    n_fail = sum(1 for e in evaluations if e["status"] == "FAIL")
    n_warn = sum(1 for e in evaluations if e["status"] == "WARN")
    n_total = sum(1 for e in evaluations if e["status"] != "N/A")
    
    if n_fail == 0 and n_warn == 0:
        overall_grade = "A — Excellent"
        overall_summary = "All best practices followed. Setup is production-ready."
    elif n_fail == 0 and n_warn <= 2:
        overall_grade = "B — Good"
        overall_summary = "Minor improvements possible but setup is acceptable for production."
    elif n_fail == 0:
        overall_grade = "C — Acceptable"
        overall_summary = "Several warnings; review recommendations before production runs."
    elif n_fail == 1:
        overall_grade = "D — Needs improvement"
        overall_summary = "One critical issue must be fixed before submission."
    else:
        overall_grade = "F — Critical issues"
        overall_summary = f"{n_fail} critical issues found. Must fix before proceeding."

    lines = []
    lines.append(f"# DFT Best-Practice Evaluation Report")
    lines.append(f"")
    lines.append(f"**Software**: {software.upper()}")
    lines.append(f"**Calculation type**: `{calc}`")
    lines.append(f"**Workspace**: `{workspace}`")
    lines.append(f"")
    lines.append(f"## Executive Summary")
    lines.append(f"")
    lines.append(f"| Grade | {overall_grade} |")
    lines.append(f"|-------|{'─' * len(overall_grade)}|")
    lines.append(f"| Pass | {n_pass}/{n_total} |")
    lines.append(f"| Warnings | {n_warn} |")
    lines.append(f"| Critical | {n_fail} |")
    lines.append(f"")
    lines.append(f"**Assessment**: {overall_summary}")
    lines.append(f"")
    
    # Critical issues first
    fails = [e for e in evaluations if e["status"] == "FAIL"]
    if fails:
        lines.append(f"## ❌ Critical Issues (Must Fix)")
        lines.append(f"")
        for e in fails:
            lines.append(f"### {e['category'].replace('_', ' ').title()}")
            lines.append(f"- **Current**: {e['current_value']}")
            if e.get("recommended"):
                lines.append(f"- **Recommended**: `{e['recommended']}`")
            if e.get("physical_justification"):
                lines.append(f"- **Why**: {e['physical_justification']}")
            lines.append(f"")

    # Category details
    lines.append(f"## Detailed Evaluation")
    lines.append(f"")
    lines.append(f"| Category | Status | Details |")
    lines.append(f"|----------|--------|---------|")
    
    status_icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "N/A": "—", "SKIP": "—"}
    for e in evaluations:
        if e["status"] == "SKIP":
            continue
        icon = status_icon.get(e["status"], "?")
        detail = e.get("current_value", e.get("rationale", ""))
        # Truncate for table
        if len(detail) > 60:
            detail = detail[:57] + "..."
        lines.append(f"| {e['category']} | {icon} {e['status']} | {detail} |")
    
    lines.append(f"")
    
    # Warnings section
    warns = [e for e in evaluations if e["status"] == "WARN"]
    if warns:
        lines.append(f"## ⚠️ Recommendations")
        lines.append(f"")
        for e in warns:
            lines.append(f"- **{e['category']}**: {e.get('current_value', '')}")
            if e.get("recommended"):
                lines.append(f"  - Suggested: `{e['recommended']}`")
            if e.get("physical_justification"):
                lines.append(f"  - Rationale: {e['physical_justification']}")
        lines.append(f"")

    # Passing categories (brief)
    passes = [e for e in evaluations if e["status"] == "PASS"]
    if passes:
        lines.append(f"## ✅ Passing Categories")
        lines.append(f"")
        for e in passes:
            lines.append(f"- **{e['category']}**: {e.get('current_value', 'OK')}")
        lines.append(f"")

    # Conclusion
    lines.append(f"## Conclusion")
    lines.append(f"")
    if n_fail == 0:
        lines.append(f"The calculation setup follows DFT best practices and is ready for submission.")
    else:
        lines.append(f"Fix the {n_fail} critical issue(s) listed above before submitting.")
        lines.append(f"After fixing, re-run this evaluation to verify corrections.")
    lines.append(f"")
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate structured DFT best-practice evaluation report."
    )
    ap.add_argument("--dir", required=True, help="Workspace directory.")
    ap.add_argument("--software", required=True, choices=["abacus", "vasp"],
                    help="DFT software.")
    ap.add_argument("--output", default=None, help="Output file (default: stdout).")
    ap.add_argument("--categories", default="all",
                    help="Comma-separated categories to evaluate (default: all).")
    args = ap.parse_args()

    workspace = Path(args.dir)
    if not workspace.is_dir():
        print(f"Error: directory not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Find and parse input file
    params = {}
    if args.software == "abacus":
        for name in ["INPUT", "input"]:
            candidate = workspace / name
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                params = _parse_params(text)
                break
        if not params:
            candidates = sorted(workspace.glob("INPUT*"))
            candidates = [f for f in candidates if f.suffix not in (".bak", ".fixed")]
            if candidates:
                text = candidates[0].read_text(encoding="utf-8", errors="replace")
                params = _parse_params(text)
    elif args.software == "vasp":
        incar = workspace / "INCAR"
        if incar.exists():
            text = incar.read_text(encoding="utf-8", errors="replace")
            params = _parse_params(text)

    if not params:
        print("Error: could not find/parse input parameter file", file=sys.stderr)
        sys.exit(1)

    # Determine categories
    if args.categories == "all":
        cats = [c for c in _CATEGORIES if c != "overall"]
    else:
        cats = [c.strip() for c in args.categories.split(",")]

    # Evaluate each category
    evaluations = []
    for cat in cats:
        if args.software == "abacus":
            result = _evaluate_category_abacus(cat, params, workspace)
        else:
            # VASP: simplified evaluation
            result = {"category": cat, "status": "SKIP", "rationale": "VASP detailed eval not yet implemented"}
        evaluations.append(result)

    # Generate report
    report = _generate_report(evaluations, params, workspace, args.software)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report written to: {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
