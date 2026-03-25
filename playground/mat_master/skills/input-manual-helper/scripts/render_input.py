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

from engine.renderer import RenderIntent
from engine.schema import SchemaRegistry


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


def _parse_params(param_list: list[str]) -> dict:
    """将 KEY=VALUE 列表解析为 dict。"""
    params: dict = {}
    for item in param_list:
        if "=" not in item:
            print(f"Warning: ignoring malformed param '{item}' (expected KEY=VALUE)", file=sys.stderr)
            continue
        key, _, value = item.partition("=")
        params[key.strip()] = value.strip()
    return params


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a calculation input file for the specified software and task."
    )
    parser.add_argument(
        "--software", required=True,
        help="Software name: cp2k, orca, qe, abinit, lammps",
    )
    parser.add_argument(
        "--task", default="scf",
        help="Task type (default: scf). Examples: scf, opt, md, sp, relax, minimize",
    )
    parser.add_argument(
        "--structure", default=None, metavar="FILE",
        help=(
            "Path to a structure file (CIF, POSCAR, XYZ, etc.) to embed in the "
            "generated input. Requires pymatgen. If omitted, a built-in Si "
            "diamond structure is used as placeholder."
        ),
    )
    parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="Override parameter (repeatable). Example: --param CUTOFF=400",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path. If omitted, writes to stdout.",
    )
    args = parser.parse_args()

    backend = _get_backend(args.software)
    params = _parse_params(args.param)

    intent = RenderIntent(
        software=args.software.lower(),
        task_type=args.task,
        structure_file=args.structure,
        params=params,
    )
    text = backend.render(intent)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(text, end="")

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
        print(f"Warning: post-generation diagnostics failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
