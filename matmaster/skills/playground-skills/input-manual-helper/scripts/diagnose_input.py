"""
diagnose_input.py — 对已有输入文件进行诊断，报告问题，可选自动修复。

Usage
-----
  python diagnose_input.py --software cp2k --input pw.in
  python diagnose_input.py --software qe --input - --format json
  python diagnose_input.py --software abacus --input INPUT --fix
  python diagnose_input.py --software abacus --input INPUT --fix --fix-output INPUT_fixed
  cat pw.in | python diagnose_input.py --software qe --input - --format human

When --fix is given for ABACUS, generates a corrected INPUT file that resolves
all auto-fixable errors (missing cal_force, missing dipole correction, etc.).
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from engine.schema import SchemaRegistry  # noqa: E402


def _get_backend(software: str):
    """根据软件名返回对应 Backend 实例。"""
    from engine.software.abacus import AbacusBackend
    from engine.software.abinit import ABINITBackend
    from engine.software.cp2k import CP2KBackend
    from engine.software.lammps import LAMMPSBackend
    from engine.software.orca import ORCABackend
    from engine.software.qe import QEBackend

    backends = {
        "cp2k": CP2KBackend,
        "orca": ORCABackend,
        "qe": QEBackend,
        "quantum-espresso": QEBackend,
        "abinit": ABINITBackend,
        "lammps": LAMMPSBackend,
        "abacus": AbacusBackend,
    }
    key = software.lower().strip()
    if key not in backends:
        supported = ", ".join(sorted(set(backends.keys())))
        print(
            f"Error: unsupported software '{software}'. Supported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)
    return backends[key]()


# ---------------------------------------------------------------------------
# ABACUS auto-fix logic
# ---------------------------------------------------------------------------


def _parse_abacus_params(text: str) -> dict[str, str]:
    """Parse ABACUS INPUT file into {key_lower: raw_value}."""
    params = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^\s*INPUT_PARAMETERS\s*$", stripped, re.IGNORECASE):
            continue
        if "#" in stripped:
            stripped = stripped[: stripped.index("#")].strip()
        parts = stripped.split(None, 1)
        if len(parts) >= 2:
            params[parts[0].lower()] = parts[1].strip()
        elif len(parts) == 1:
            params[parts[0].lower()] = ""
    return params


def _abacus_auto_fix(text: str, diagnostics: list) -> tuple[str, list[str]]:
    """Apply automatic fixes to an ABACUS INPUT file based on diagnostics.
    Returns (fixed_text, list_of_fixes_applied).
    """
    params = _parse_abacus_params(text)
    fixes_applied = []
    calc = params.get("calculation", "scf").lower()

    # Fix rules based on diagnostics
    for d in diagnostics:
        if d.severity != "error":
            continue
        msg_lower = d.message.lower() if d.message else ""
        param = d.param if hasattr(d, "param") and d.param else ""

        # cal_force fix for relax/cell-relax/md
        if "cal_force" in msg_lower and param == "cal_force":
            if calc in ("relax", "cell-relax", "md"):
                params["cal_force"] = "1"
                fixes_applied.append(f"cal_force → 1 (required for {calc})")

        # cal_stress fix for cell-relax
        if "cal_stress" in msg_lower and param == "cal_stress":
            if calc == "cell-relax":
                params["cal_stress"] = "1"
                fixes_applied.append("cal_stress → 1 (required for cell-relax)")

        # init_chg fix for NSCF
        if "init_chg" in msg_lower and param == "init_chg":
            if calc == "nscf":
                params["init_chg"] = "file"
                fixes_applied.append("init_chg → file (required for NSCF)")

        # symmetry fix for NSCF
        if "symmetry" in msg_lower and param == "symmetry":
            if calc == "nscf":
                params["symmetry"] = "0"
                fixes_applied.append("symmetry → 0 (required for NSCF band/DOS)")

        # noncolin/nspin fix
        if "noncolin=1 requires nspin=4" in msg_lower:
            params["nspin"] = "4"
            fixes_applied.append("nspin → 4 (required for noncolin=1)")

    # Additional auto-fixes from warnings (critical best practices)
    for d in diagnostics:
        if d.severity != "warning":
            continue
        msg_lower = d.message.lower() if d.message else ""
        param = d.param if hasattr(d, "param") and d.param else ""

        # out_pot=2 without dipole correction → add dipole correction
        if "efield_flag" in param and "out_pot" in msg_lower:
            params.setdefault("efield_flag", "1")
            params.setdefault("dip_cor_flag", "1")
            params.setdefault("efield_dir", "2")
            params.setdefault("efield_pos_max", "0.0")
            params.setdefault("efield_pos_dec", "0.1")
            params.setdefault("efield_amp", "0.0")
            fixes_applied.append(
                "Added dipole correction (efield_flag 1, dip_cor_flag 1, efield_dir 2, "
                "efield_pos_max 0.0, efield_pos_dec 0.1, efield_amp 0.0) "
                "for slab electrostatic potential"
            )

    # Ensure standard baseline params
    if "mixing_type" not in params:
        params["mixing_type"] = "broyden"
        fixes_applied.append("mixing_type → broyden (ABACUS standard)")

    if "ecutwfc" not in params:
        params["ecutwfc"] = "100"
        fixes_applied.append("ecutwfc → 100 (ABACUS standard)")

    if not fixes_applied:
        return text, []

    # Re-render INPUT with fixes applied
    lines = ["INPUT_PARAMETERS"]
    category_order = [
        [
            "suffix",
            "ntype",
            "calculation",
            "esolver_type",
            "pseudo_dir",
            "orbital_dir",
            "stru_file",
            "kpoint_file",
            "symmetry",
        ],
        [
            "ecutwfc",
            "basis_type",
            "nspin",
            "nbands",
            "dft_functional",
            "gamma_only",
            "kspacing",
            "smearing_method",
            "smearing_sigma",
            "ks_solver",
            "noncolin",
            "lspinorb",
            "lda_plus_u",
            "hubbard_u",
            "orbital_corr",
            "nupdown",
            "vdw_method",
        ],
        [
            "scf_thr",
            "scf_nmax",
            "mixing_type",
            "mixing_beta",
            "mixing_ndim",
            "mixing_gg0",
            "init_chg",
        ],
        [
            "cal_force",
            "cal_stress",
            "force_thr_ev",
            "stress_thr",
            "relax_nmax",
            "relax_method",
            "fixed_atoms",
        ],
        [
            "md_type",
            "md_nstep",
            "md_dt",
            "md_tfirst",
            "md_tlast",
            "md_tfreq",
            "md_dumpfreq",
            "md_restartfreq",
            "init_vel",
        ],
        [
            "efield_flag",
            "dip_cor_flag",
            "efield_dir",
            "efield_amp",
            "efield_pos_max",
            "efield_pos_dec",
            "gate_flag",
            "zgate",
            "block",
            "block_down",
            "block_up",
            "block_height",
        ],
        [
            "out_chg",
            "out_dos",
            "out_band",
            "out_proj_band",
            "out_stru",
            "out_pot",
            "out_wfc_lcao",
            "out_dipole",
            "out_mul",
        ],
    ]

    emitted: set = set()
    for group in category_order:
        group_lines = []
        for key in group:
            if key in params:
                val = params[key]
                group_lines.append(f"{key:<24}{val}")
                emitted.add(key)
        if group_lines:
            lines.append("")
            lines.extend(group_lines)

    extras = [(k, v) for k, v in params.items() if k not in emitted]
    if extras:
        lines.append("")
        for k, v in extras:
            lines.append(f"{k:<24}{v}")

    return "\n".join(lines) + "\n", fixes_applied


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose a calculation input file and report issues."
    )
    parser.add_argument(
        "--software",
        required=True,
        help="Software name: cp2k, orca, qe, abinit, lammps, abacus",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE_OR_-",
        help="Input file path, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human).",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix: generate a corrected INPUT file resolving all fixable errors. "
        "Currently supported for ABACUS. Writes corrected file to --fix-output.",
    )
    parser.add_argument(
        "--fix-output",
        default=None,
        metavar="FILE",
        help="Output path for fixed file (default: <input>_fixed or stdout if input is -).",
    )
    args = parser.parse_args()

    if args.input == "-":
        text = sys.stdin.read()
        source = "<stdin>"
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        text = input_path.read_text(encoding="utf-8", errors="replace")
        source = str(input_path)

    backend = _get_backend(args.software)
    schema = SchemaRegistry()
    schema.load_software(args.software.lower())

    doc = backend.parse(text, source)
    diagnostics = backend.get_diagnostics(doc, schema)

    if args.format == "json":
        result_dict: dict = {"diagnostics": [d.to_dict() for d in diagnostics]}
    else:
        if not diagnostics:
            print("No issues found.")
        for d in diagnostics:
            print(d.to_human())

    has_error = any(d.severity == "error" for d in diagnostics)

    # Auto-fix mode
    if args.fix:
        sw = args.software.lower().strip()
        if sw == "abacus":
            fixed_text, fixes_applied = _abacus_auto_fix(text, diagnostics)
            if fixes_applied:
                # Determine output path
                if args.fix_output:
                    fix_path = Path(args.fix_output)
                elif args.input != "-":
                    fix_path = Path(args.input + "_fixed")
                else:
                    fix_path = None

                if fix_path:
                    fix_path.write_text(fixed_text, encoding="utf-8")
                    print(f"\n{'─'*50}", file=sys.stderr)
                    print(f"✅ Fixed INPUT written to: {fix_path}", file=sys.stderr)
                    print(f"   Fixes applied ({len(fixes_applied)}):", file=sys.stderr)
                    for f in fixes_applied:
                        print(f"     • {f}", file=sys.stderr)
                    print(f"{'─'*50}", file=sys.stderr)
                else:
                    # Write to stdout
                    print("\n--- FIXED INPUT ---")
                    print(fixed_text)
                    print(f"--- Fixes applied: {len(fixes_applied)} ---")
                    for f in fixes_applied:
                        print(f"  • {f}")

                if args.format == "json":
                    result_dict["fixed"] = True
                    result_dict["fixes_applied"] = fixes_applied
                    if fix_path:
                        result_dict["fixed_file"] = str(fix_path)
            else:
                if args.format != "json":
                    print("\nNo auto-fixable issues found.", file=sys.stderr)
                if args.format == "json":
                    result_dict["fixed"] = False
                    result_dict["fixes_applied"] = []
        else:
            print(
                "\nNote: --fix is currently supported for ABACUS only.",
                file=sys.stderr,
            )
            if args.format == "json":
                result_dict["fixed"] = False
                result_dict["fixes_applied"] = []

    if args.format == "json":
        print(json.dumps(result_dict, ensure_ascii=False, indent=2))

    # Cross-reference related tools (aid discovery)
    _print_related_tools(args.software, has_error, diagnostics, args.format)

    sys.exit(1 if has_error else 0)


def _print_related_tools(
    software: str, has_error: bool, diagnostics: list, fmt: str
) -> None:
    """Print cross-references to related scripts for tool discovery."""
    if fmt == "json":
        return  # don't pollute JSON output
    sw = software.lower().strip()
    print(f"\n{'─'*50}", file=sys.stderr)
    print("📋 Related tools in this skill:", file=sys.stderr)
    if sw == "abacus":
        print(
            "  • preflight_abacus.py --dir .        → Full workspace validation (INPUT + STRU + KPT cross-check)",
            file=sys.stderr,
        )
        print(
            "  • evaluate_dft_setup.py --software abacus --dir .  → Best-practice grade (12 categories)",
            file=sys.stderr,
        )
        print(
            "  • render_abacus_workflow.py --workflow <type> --output-dir ./  → One-shot workflow generation",
            file=sys.stderr,
        )
        if has_error:
            print(
                "  • diagnose_input.py --software abacus --input INPUT --fix  → Auto-fix mode",
                file=sys.stderr,
            )
    elif sw == "vasp":
        print(
            "  • evaluate_dft_setup.py --software vasp --dir .  → Best-practice evaluation",
            file=sys.stderr,
        )
        print(
            "  • generate_kpoints.py --structure POSCAR --mode auto  → KPOINTS generation",
            file=sys.stderr,
        )
    else:
        print(
            f"  • evaluate_dft_setup.py --software {sw} --dir .  → Best-practice check (if supported)",
            file=sys.stderr,
        )
    print(f"{'─'*50}", file=sys.stderr)


if __name__ == "__main__":
    main()
