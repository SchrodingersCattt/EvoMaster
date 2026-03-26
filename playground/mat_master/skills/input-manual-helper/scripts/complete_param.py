"""
complete_param.py — 在指定光标位置提供参数补全建议。

Usage
-----
  python complete_param.py --software cp2k --input pw.in --line 1 --col 0
  cat pw.in | python complete_param.py --software qe --input - --line 3 --col 4 --format json
"""

import argparse
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provide parameter completion suggestions at a given cursor position."
    )
    parser.add_argument(
        "--software", required=True,
        help="Software name: cp2k, orca, qe, abinit, lammps",
    )
    parser.add_argument(
        "--input", required=True, metavar="FILE_OR_-",
        help="Input file path, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--line", required=True, type=int,
        help="Cursor line number (1-based).",
    )
    parser.add_argument(
        "--col", required=True, type=int,
        help="Cursor column number (0-based).",
    )
    parser.add_argument(
        "--format", choices=["human", "json"], default="human",
        help="Output format (default: human).",
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
    items = backend.get_completions(doc, args.line, args.col, schema)

    if args.format == "json":
        result = [
            {
                "label": item.label,
                "detail": item.detail,
                "documentation": item.documentation,
                "insert_text": item.insert_text,
                "category": item.category,
            }
            for item in items
        ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not items:
            print("No completions available at this position.")
        for item in items:
            print(f"{item.label:<30} ({item.category}) — {item.detail}")


if __name__ == "__main__":
    main()
