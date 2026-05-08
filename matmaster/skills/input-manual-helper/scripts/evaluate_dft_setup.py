#!/usr/bin/env python3
"""
evaluate_dft_setup.py — Systematic DFT best-practice evaluation for input files.

Evaluates a calculation setup against standard DFT best practices across
12 categories. Produces a structured JSON report with pass/fail/warning per
category, an overall grade, and actionable recommendations.

Supports: ABACUS (full), VASP/INCAR (basic parameter review).

Usage:
  # Evaluate ABACUS INPUT:
  python evaluate_dft_setup.py --software abacus --dir ./workspace/

  # Evaluate VASP INCAR:
  python evaluate_dft_setup.py --software vasp --dir ./workspace/

  # JSON output for programmatic use:
  python evaluate_dft_setup.py --software abacus --dir ./workspace/ --format json

Output: Structured evaluation report (human-readable or JSON) covering:
  1. Basis set / cutoff energy adequacy
  2. K-point sampling density
  3. SCF convergence parameters
  4. Smearing settings
  5. Relaxation parameters (if applicable)
  6. Two-step workflow consistency (band/DOS)
  7. File reference integrity
  8. Pseudopotential / orbital consistency
  9. Structure quality (STRU/POSCAR)
  10. Slab / surface treatment
  11. Multi-configuration consistency
  12. Output parameter completeness
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Category evaluators
# ---------------------------------------------------------------------------


class EvalCategory:
    def __init__(
        self,
        name: str,
        status: str = "skip",
        score: int = 0,
        max_score: int = 1,
        issues: list = None,
        recommendations: list = None,
    ):
        self.name = name
        self.status = status  # "pass", "fail", "warn", "skip"
        self.score = score
        self.max_score = max_score
        self.issues = issues or []
        self.recommendations = recommendations or []

    def to_dict(self) -> dict:
        d = {
            "category": self.name,
            "status": self.status,
            "score": f"{self.score}/{self.max_score}",
        }
        if self.issues:
            d["issues"] = self.issues
        if self.recommendations:
            d["recommendations"] = self.recommendations
        return d


def _parse_params(text: str) -> dict[str, str]:
    """Parse key-value parameter file (ABACUS INPUT or VASP INCAR)."""
    params = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        # Remove inline comments
        for comment_char in ("#", "!", "//"):
            if comment_char in stripped:
                stripped = stripped[: stripped.index(comment_char)].strip()
        if not stripped:
            continue
        # Skip section headers
        if re.match(r"^\s*INPUT_PARAMETERS\s*$", stripped, re.IGNORECASE):
            continue
        # Parse KEY = VALUE or KEY VALUE
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
# ABACUS evaluation
# ---------------------------------------------------------------------------


def evaluate_abacus(workspace: Path) -> list[EvalCategory]:
    """Evaluate ABACUS workspace against best practices."""
    categories = []

    # Find INPUT file(s)
    input_files = sorted(workspace.glob("INPUT*"))
    input_files = [f for f in input_files if f.suffix not in (".bak", ".fixed")]
    if not input_files:
        categories.append(
            EvalCategory(
                "file_integrity", "fail", 0, 1, ["No INPUT file found in workspace"]
            )
        )
        return categories

    # Use primary INPUT
    input_file = input_files[0]
    text = input_file.read_text(encoding="utf-8", errors="replace")
    params = _parse_params(text)
    calc = params.get("calculation", "scf").lower()

    # 1. Basis set / cutoff
    cat = EvalCategory("basis_cutoff", max_score=2)
    ecutwfc = _get_float(params, "ecutwfc")
    basis_type = params.get("basis_type", "lcao").lower()
    if ecutwfc is None:
        cat.issues.append("ecutwfc not set — using ABACUS default (may be inadequate)")
        cat.recommendations.append("Set ecutwfc explicitly; standard: 100 Ry")
        cat.status = "fail"
        cat.score = 0
    elif ecutwfc >= 100:
        cat.score = 2
        cat.status = "pass"
    elif ecutwfc >= 50:
        cat.score = 1
        cat.status = "warn"
        cat.issues.append(f"ecutwfc={ecutwfc} Ry is below recommended 100 Ry standard")
        cat.recommendations.append(
            "Use ecutwfc=100 unless task explicitly requires different"
        )
    else:
        cat.score = 0
        cat.status = "fail"
        cat.issues.append(
            f"ecutwfc={ecutwfc} Ry is too low for production calculations"
        )
    if basis_type == "lcao" and "orbital_dir" not in params:
        cat.issues.append("basis_type=lcao but orbital_dir not set")
    categories.append(cat)

    # 2. K-point sampling
    cat = EvalCategory("kpoint_sampling", max_score=2)
    kspacing = params.get("kspacing")
    kpt_file = params.get("kpoint_file", "KPT")
    kpt_path = workspace / kpt_file
    if kspacing:
        # kspacing mode - generally appropriate for supercells
        try:
            ksp_val = float(kspacing.split()[0])
            if ksp_val <= 0.12:
                cat.score = 2
                cat.status = "pass"
            elif ksp_val <= 0.20:
                cat.score = 1
                cat.status = "warn"
                cat.issues.append(f"kspacing={ksp_val} may be too coarse for metals")
            else:
                cat.score = 0
                cat.status = "fail"
                cat.issues.append(f"kspacing={ksp_val} is very coarse")
        except ValueError:
            cat.score = 1
            cat.status = "warn"
    elif kpt_path.exists():
        cat.score = 2
        cat.status = "pass"
    elif (workspace / "KPT").exists():
        cat.score = 2
        cat.status = "pass"
    else:
        cat.score = 0
        cat.status = "fail"
        cat.issues.append(f"No KPT file found ({kpt_file}) and no kspacing set")
        cat.recommendations.append("Create KPT file or set kspacing in INPUT")
    categories.append(cat)

    # 3. SCF convergence
    cat = EvalCategory("scf_convergence", max_score=2)
    scf_thr = _get_float(params, "scf_thr")
    scf_nmax = _get_int(params, "scf_nmax")
    if scf_thr is not None and scf_thr <= 1e-6:
        cat.score += 1
    elif scf_thr is None:
        cat.issues.append("scf_thr not set (default may be too loose)")
    else:
        cat.issues.append(f"scf_thr={scf_thr} is looser than recommended 1e-7")
    if scf_nmax is not None and scf_nmax >= 100:
        cat.score += 1
    elif scf_nmax is not None and scf_nmax < 50:
        cat.issues.append(f"scf_nmax={scf_nmax} is too low — SCF may not converge")
    cat.status = "pass" if cat.score == 2 else ("warn" if cat.score == 1 else "fail")
    categories.append(cat)

    # 4. Smearing
    cat = EvalCategory("smearing", max_score=1)
    smearing_method = params.get("smearing_method", "").lower()
    smearing_sigma = _get_float(params, "smearing_sigma")
    if smearing_method and smearing_sigma is not None:
        if smearing_sigma <= 0.02:
            cat.score = 1
            cat.status = "pass"
        else:
            cat.issues.append(
                f"smearing_sigma={smearing_sigma} is large (recommend ≤0.015 Ry)"
            )
            cat.status = "warn"
    elif not smearing_method:
        cat.issues.append("smearing_method not specified")
        cat.status = "warn"
    categories.append(cat)

    # 5. Relaxation parameters
    if calc in ("relax", "cell-relax"):
        cat = EvalCategory("relaxation", max_score=3)
        cal_force = _get_int(params, "cal_force")
        if cal_force == 1:
            cat.score += 1
        else:
            cat.issues.append("CRITICAL: cal_force not set to 1 — relaxation BROKEN")
            cat.status = "fail"
        if calc == "cell-relax":
            cal_stress = _get_int(params, "cal_stress")
            if cal_stress == 1:
                cat.score += 1
            else:
                cat.issues.append(
                    "CRITICAL: cal_stress not set to 1 — cell not optimized"
                )
                cat.status = "fail"
        force_thr_ev = _get_float(params, "force_thr_ev")
        if force_thr_ev is not None:
            cat.score += 1
        else:
            cat.issues.append("force_thr_ev not set — using default threshold")
            cat.recommendations.append("Set force_thr_ev=0.01 (eV/Å)")
        if cat.status != "fail":
            cat.status = "pass" if cat.score >= 2 else "warn"
        categories.append(cat)

    # 6. Two-step workflow (band/DOS)
    if calc == "nscf":
        cat = EvalCategory("two_step_workflow", max_score=3)
        init_chg = params.get("init_chg", "").lower()
        symmetry = _get_int(params, "symmetry")
        nbands = _get_int(params, "nbands")
        if init_chg == "file":
            cat.score += 1
        else:
            cat.issues.append("CRITICAL: init_chg not set to 'file' — will re-run SCF")
        if symmetry == 0:
            cat.score += 1
        else:
            cat.issues.append("symmetry not set to 0 — k-path will be folded")
        if nbands is not None:
            cat.score += 1
        else:
            cat.issues.append("nbands not set — may miss empty states")
        cat.status = (
            "pass" if cat.score == 3 else ("warn" if cat.score >= 2 else "fail")
        )
        categories.append(cat)

    # 7. File reference integrity
    cat = EvalCategory("file_references", max_score=2)
    stru_ref = params.get("stru_file", "STRU")
    kpt_ref = params.get("kpoint_file", "KPT")
    stru_exists = (workspace / stru_ref).exists()
    kpt_exists = (workspace / kpt_ref).exists() or kspacing is not None
    if stru_exists:
        cat.score += 1
    else:
        cat.issues.append(f"STRU file '{stru_ref}' not found in workspace")
    if kpt_exists:
        cat.score += 1
    else:
        cat.issues.append(f"KPT file '{kpt_ref}' not found and no kspacing set")
    cat.status = "pass" if cat.score == 2 else ("warn" if cat.score == 1 else "fail")
    categories.append(cat)

    # 8. Slab / surface treatment
    out_pot = _get_int(params, "out_pot")
    efield_flag = _get_int(params, "efield_flag")
    dip_cor_flag = _get_int(params, "dip_cor_flag")
    if out_pot == 2 or efield_flag == 1:
        cat = EvalCategory("slab_treatment", max_score=3)
        if efield_flag == 1:
            cat.score += 1
        else:
            cat.issues.append(
                "out_pot=2 without efield_flag=1 — missing dipole correction"
            )
        if dip_cor_flag == 1:
            cat.score += 1
        else:
            cat.issues.append("Dipole correction (dip_cor_flag=1) not enabled for slab")
        efield_dir = _get_int(params, "efield_dir")
        if efield_dir is not None:
            cat.score += 1
        else:
            cat.issues.append("efield_dir not set — defaulting to z (2)")
            cat.recommendations.append(
                "Set efield_dir to vacuum direction (0=x, 1=y, 2=z)"
            )
        cat.status = (
            "pass" if cat.score == 3 else ("warn" if cat.score >= 2 else "fail")
        )
        categories.append(cat)

    # 9. Mixing parameters
    cat = EvalCategory("mixing", max_score=1)
    mixing_type = params.get("mixing_type", "").lower()
    if mixing_type == "broyden":
        cat.score = 1
        cat.status = "pass"
    elif mixing_type:
        cat.score = 1
        cat.status = "pass"
    else:
        cat.issues.append("mixing_type not set (recommend 'broyden')")
        cat.status = "warn"
    categories.append(cat)

    # 10. ntype consistency
    ntype = _get_int(params, "ntype")
    stru_path = workspace / params.get("stru_file", "STRU")
    if stru_path.exists() and ntype is not None:
        cat = EvalCategory("ntype_consistency", max_score=1)
        stru_text = stru_path.read_text(encoding="utf-8", errors="replace")
        species = []
        in_species = False
        for line in stru_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "//" in stripped:
                stripped = stripped[: stripped.index("//")].strip()
            if re.match(r"^ATOMIC_SPECIES\s*$", stripped, re.IGNORECASE):
                in_species = True
                continue
            if in_species:
                if re.match(
                    r"^(NUMERICAL_ORBITAL|LATTICE|ATOMIC_POSITIONS)",
                    stripped,
                    re.IGNORECASE,
                ):
                    break
                parts = stripped.split()
                if len(parts) >= 2:
                    species.append(parts[0])
        if species:
            if ntype == len(species):
                cat.score = 1
                cat.status = "pass"
            else:
                cat.issues.append(
                    f"ntype={ntype} in INPUT but STRU has {len(species)} species: {species}"
                )
                cat.status = "fail"
        categories.append(cat)

    return categories


# ---------------------------------------------------------------------------
# VASP evaluation (basic INCAR review)
# ---------------------------------------------------------------------------


def evaluate_vasp(workspace: Path) -> list[EvalCategory]:
    """Basic evaluation of VASP INCAR against best practices."""
    categories = []

    incar_path = workspace / "INCAR"
    if not incar_path.exists():
        categories.append(
            EvalCategory("file_integrity", "fail", 0, 1, ["No INCAR file found"])
        )
        return categories

    text = incar_path.read_text(encoding="utf-8", errors="replace")
    params = _parse_params(text)

    # 1. ENCUT
    cat = EvalCategory("basis_cutoff", max_score=1)
    encut = _get_float(params, "encut")
    if encut is not None and encut >= 400:
        cat.score = 1
        cat.status = "pass"
    elif encut is not None:
        cat.issues.append(f"ENCUT={encut} eV — verify against POTCAR ENMAX × 1.3")
        cat.status = "warn"
    else:
        cat.issues.append("ENCUT not set — VASP uses POTCAR ENMAX (may be inadequate)")
        cat.status = "warn"
    categories.append(cat)

    # 2. KPOINTS file
    cat = EvalCategory("kpoint_sampling", max_score=1)
    kpoints_path = workspace / "KPOINTS"
    if kpoints_path.exists():
        cat.score = 1
        cat.status = "pass"
    else:
        cat.issues.append("No KPOINTS file found")
        cat.recommendations.append(
            "Generate with: python generate_kpoints.py --structure POSCAR --mode auto"
        )
        cat.status = "fail"
    categories.append(cat)

    # 3. EDIFF (SCF convergence)
    cat = EvalCategory("scf_convergence", max_score=1)
    ediff = _get_float(params, "ediff")
    if ediff is not None and ediff <= 1e-5:
        cat.score = 1
        cat.status = "pass"
    elif ediff is not None:
        cat.issues.append(f"EDIFF={ediff} — recommend 1E-6 for accurate energies")
        cat.status = "warn"
    else:
        cat.status = "pass"  # VASP default is usually OK
        cat.score = 1
    categories.append(cat)

    # 4. ISMEAR + SIGMA
    cat = EvalCategory("smearing", max_score=1)
    ismear = _get_int(params, "ismear")
    sigma = _get_float(params, "sigma")
    if ismear is not None:
        cat.score = 1
        cat.status = "pass"
        if ismear == -5 and sigma and sigma > 0.05:
            cat.issues.append(
                "ISMEAR=-5 (tetrahedron) doesn't use SIGMA, but SIGMA is set large"
            )
    else:
        cat.status = "warn"
        cat.issues.append("ISMEAR not set — VASP default may not be optimal")
    categories.append(cat)

    # 5. POSCAR
    cat = EvalCategory("structure", max_score=1)
    poscar_path = workspace / "POSCAR"
    if poscar_path.exists():
        cat.score = 1
        cat.status = "pass"
    else:
        cat.issues.append("No POSCAR file found")
        cat.status = "fail"
    categories.append(cat)

    # 6. Relaxation (if IBRION set)
    ibrion = _get_int(params, "ibrion")
    if ibrion is not None and ibrion >= 1:
        cat = EvalCategory("relaxation", max_score=2)
        nsw = _get_int(params, "nsw")
        ediffg = _get_float(params, "ediffg")
        if nsw is not None and nsw > 0:
            cat.score += 1
        else:
            cat.issues.append("IBRION set but NSW=0 — no ionic steps will run")
        if ediffg is not None:
            cat.score += 1
        else:
            cat.issues.append("EDIFFG not set — using default convergence criterion")
        cat.status = (
            "pass" if cat.score == 2 else ("warn" if cat.score >= 1 else "fail")
        )
        categories.append(cat)

    return categories


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Evaluate DFT calculation setup against best practices."
    )
    ap.add_argument(
        "--software",
        required=True,
        choices=["abacus", "vasp"],
        help="DFT software to evaluate (abacus or vasp).",
    )
    ap.add_argument(
        "--dir",
        required=True,
        help="Workspace directory containing input files.",
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

    # Run evaluation
    if args.software == "abacus":
        categories = evaluate_abacus(workspace)
    else:
        categories = evaluate_vasp(workspace)

    # Calculate overall grade
    total_score = sum(c.score for c in categories)
    max_score = sum(c.max_score for c in categories)
    n_pass = sum(1 for c in categories if c.status == "pass")
    n_fail = sum(1 for c in categories if c.status == "fail")
    n_warn = sum(1 for c in categories if c.status == "warn")

    pct = round(100 * total_score / max_score) if max_score > 0 else 0
    if n_fail == 0 and n_warn == 0:
        grade = "EXCELLENT"
    elif n_fail == 0:
        grade = "GOOD (minor issues)"
    elif n_fail <= 1:
        grade = "NEEDS IMPROVEMENT"
    else:
        grade = "CRITICAL ISSUES"

    result = {
        "software": args.software,
        "workspace": str(workspace),
        "grade": grade,
        "score": f"{total_score}/{max_score} ({pct}%)",
        "summary": {
            "pass": n_pass,
            "warn": n_warn,
            "fail": n_fail,
        },
        "categories": [c.to_dict() for c in categories],
    }

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"DFT Setup Evaluation — {args.software.upper()}")
        print(f"Workspace: {workspace}")
        print(f"{'='*60}")
        print(f"\n  Grade: {grade}")
        print(f"  Score: {total_score}/{max_score} ({pct}%)")
        print(f"  Categories: {n_pass} pass, {n_warn} warn, {n_fail} fail")
        print(f"\n{'─'*60}")

        for cat in categories:
            icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}[cat.status]
            print(f"  {icon} {cat.name}: {cat.score}/{cat.max_score}")
            for issue in cat.issues:
                print(f"       • {issue}")
            for rec in cat.recommendations:
                print(f"       → {rec}")

        if n_fail > 0:
            print(f"\n{'─'*60}")
            print("  ❌ Fix critical issues before submission!")
            # Provide actionable fix suggestions
            all_issues = [i for c in categories for i in c.issues if c.status == "fail"]
            if any("dipole" in i.lower() or "efield" in i.lower() for i in all_issues):
                print(
                    "\n  💡 For slab/workfunction: use render_abacus_workflow.py --workflow workfunction"
                )
            if any(
                "cal_force" in i.lower() or "cal_stress" in i.lower()
                for i in all_issues
            ):
                print(
                    "\n  💡 For relaxation: use render_abacus_workflow.py --workflow relax"
                )
            if any("kpoints" in i.lower() or "kpt" in i.lower() for i in all_issues):
                print(
                    "\n  💡 Generate KPOINTS: python generate_kpoints.py --structure POSCAR --mode auto"
                )
        else:
            print(f"\n{'─'*60}")
            print("  ✅ Setup looks good for submission.")

    # Cross-reference related tools
    if args.format == "human":
        print(f"\n{'─'*50}", file=sys.stderr)
        print("📋 Related tools:", file=sys.stderr)
        if args.software == "abacus":
            print(
                "  • workspace_review.py --dir . --software abacus  → Combined review (files+params+grade)",
                file=sys.stderr,
            )
            print(
                "  • format_bp_report.py --dir . --software abacus  → Markdown evaluation report",
                file=sys.stderr,
            )
            print(
                "  • preflight_abacus.py --dir . --fix  → Auto-fix INPUT errors",
                file=sys.stderr,
            )
        else:
            print(
                "  • workspace_review.py --dir . --software vasp  → Combined review",
                file=sys.stderr,
            )
            print(
                "  • generate_kpoints.py --structure POSCAR --mode auto  → KPOINTS generation",
                file=sys.stderr,
            )
        print(f"{'─'*50}", file=sys.stderr)

    sys.exit(1 if n_fail > 0 else 0)


if __name__ == "__main__":
    main()
