"""
render_input.py — 根据软件名和任务类型生成输入文件。

Usage
-----
  python render_input.py --software cp2k --task scf
  python render_input.py --software orca --task sp --param functional=PBE
  python render_input.py --software qe --task scf --output pw.in
"""

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


def _get_backend(software: str):
    """Return only the requested backend to avoid unrelated dependency imports."""
    key = software.lower().strip()
    if key == "abacus":
        from engine.software.abacus import AbacusBackend

        return AbacusBackend()
    if key == "abinit":
        from engine.software.abinit import ABINITBackend

        return ABINITBackend()
    if key == "cp2k":
        from engine.software.cp2k import CP2KBackend

        return CP2KBackend()
    if key == "lammps":
        from engine.software.lammps import LAMMPSBackend

        return LAMMPSBackend()
    if key == "orca":
        from engine.software.orca import ORCABackend

        return ORCABackend()
    if key in ("qe", "quantum-espresso"):
        from engine.software.qe import QEBackend

        return QEBackend()

    supported = "abacus, abinit, cp2k, lammps, orca, qe, quantum-espresso"
    print(
        (f"Error: unsupported software '{software}'. " f"Supported: {supported}"),
        file=sys.stderr,
    )
    sys.exit(1)


def _parse_params(param_list: list[str]) -> dict:
    """将 KEY=VALUE 列表解析为 dict。"""
    params: dict = {}
    for item in param_list:
        if "=" not in item:
            print(
                (f"Warning: ignoring malformed param '{item}' " "(expected KEY=VALUE)"),
                file=sys.stderr,
            )
            continue
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()
    return params


def _parse_overrides(overrides_text: str | None) -> dict:
    """将 --overrides 的 JSON 字符串解析为 dict。"""
    if not overrides_text:
        return {}
    try:
        parsed = json.loads(overrides_text)
    except json.JSONDecodeError as exc:
        print(
            f"Error: invalid --overrides JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(parsed, dict):
        print(
            "Error: --overrides must decode to a JSON object",
            file=sys.stderr,
        )
        sys.exit(1)
    return parsed


def main() -> None:
    from engine.renderer import RenderIntent
    from engine.schema import SchemaRegistry

    parser = argparse.ArgumentParser(
        description=(
            "Generate a calculation input file for the specified " "software and task."
        )
    )
    parser.add_argument(
        "--software",
        required=True,
        help="Software name: cp2k, orca, qe, abinit, lammps, abacus",
    )
    parser.add_argument(
        "--task",
        default="scf",
        help=(
            "Task type (default: scf). Examples: scf, opt, md, sp, "
            "relax, minimize, "
            "band, dos, cell-relax, workfunction, dftu, eos"
        ),
    )
    parser.add_argument(
        "--structure",
        default=None,
        metavar="FILE",
        help=(
            "Path to a structure file (CIF, POSCAR, XYZ, etc.) "
            "to embed in the "
            "generated input. Requires pymatgen. If omitted, a built-in Si "
            "diamond structure is used as placeholder."
        ),
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override parameter (repeatable). Example: --param CUTOFF=400",
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help=(
            "JSON object with parameter overrides. Example: "
            """--overrides '{"ecutwfc": 100}'"""
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path. If omitted, writes to stdout.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Output directory for multi-file generation (ABACUS: INPUT, STRU, KPT). "
            "If omitted for ABACUS, writes all files to current directory when "
            "--output is also omitted, or only INPUT when --output is given."
        ),
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help=(
            "Generate all required input files (ABACUS: INPUT + STRU + KPT). "
            "Implied when --output-dir is given. For ABACUS without this flag, "
            "only INPUT is produced."
        ),
    )
    args = parser.parse_args()

    backend = _get_backend(args.software)
    params = _parse_params(args.param)
    params.update(_parse_overrides(args.overrides))

    intent = RenderIntent(
        software=args.software.lower(),
        task_type=args.task,
        structure_file=args.structure,
        params=params,
    )

    # ABACUS multi-file mode: generate INPUT + STRU + KPT
    is_abacus = args.software.lower().strip() == "abacus"
    do_all_files = args.all_files or args.output_dir is not None

    if is_abacus and do_all_files:
        files = backend.render_all(intent)
        out_dir = Path(args.output_dir) if args.output_dir else Path(".")
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for fname, content in sorted(files.items()):
            fpath = out_dir / fname
            fpath.write_text(content, encoding="utf-8")
            written.append(str(fpath))
        print(
            f"ABACUS multi-file output: {', '.join(written)}",
            file=sys.stderr,
        )
        # Print INPUT to stdout as well for convenience
        print(files.get("INPUT", ""), end="")
        text = files.get("INPUT", "")
    else:
        text = backend.render(intent)

        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(text, end="")

        # For ABACUS without --all-files, still hint about companion files
        if is_abacus and not args.output_dir:
            print(
                "\nNote: ABACUS requires STRU and KPT files too. "
                "Use --all-files or --output-dir to generate all three.",
                file=sys.stderr,
            )

    # 诊断生成的内容，若有 error 则在 stderr 输出警告
    try:
        schema = SchemaRegistry()
        schema.load_software(args.software.lower())
        doc = backend.parse(text)
        diagnostics = backend.get_diagnostics(doc, schema)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            print(
                f"\nWarning: {len(errors)} error(s) found in generated input:",
                file=sys.stderr,
            )
            for d in errors:
                print(f"  {d.to_human()}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Warning: post-generation diagnostics failed: {exc}",
            file=sys.stderr,
        )

    # Suggest next steps for validation
    sw = args.software.lower().strip()
    print(f"\n{'─'*50}", file=sys.stderr)
    print("Next steps:", file=sys.stderr)
    if sw == "abacus":
        out_dir_str = args.output_dir or "."
        print(
            "  1. python diagnose_input.py --software abacus --input INPUT --fix",
            file=sys.stderr,
        )
        print(
            f"  2. python preflight_abacus.py --dir {out_dir_str}  → Cross-check INPUT+STRU+KPT",
            file=sys.stderr,
        )
        print(
            f"  3. python evaluate_dft_setup.py --software abacus --dir {out_dir_str}  → Best-practice grade",
            file=sys.stderr,
        )
    elif sw == "vasp":
        print(
            "  1. python evaluate_dft_setup.py --software vasp --dir .  → Best-practice evaluation",
            file=sys.stderr,
        )
        print(
            "  2. python generate_kpoints.py --structure POSCAR --mode auto  → KPOINTS",
            file=sys.stderr,
        )
    else:
        print(
            f"  1. python diagnose_input.py --software {sw} --input <output>  → Validate",
            file=sys.stderr,
        )
    print(f"{'─'*50}", file=sys.stderr)


if __name__ == "__main__":
    main()
