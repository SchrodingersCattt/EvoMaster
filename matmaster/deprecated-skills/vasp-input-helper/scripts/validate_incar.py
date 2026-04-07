"""Validate VASP INCAR file against constraints and best practices.

Usage:
  python validate_incar.py --input-file /path/to/INCAR --task-type relax
  python validate_incar.py --input-file /path/to/INCAR --task-type md --is-metal --enmax 400
"""

import argparse
import sys
from pathlib import Path

_KNOWLEDGE_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "vasp_wiki" / "knowledge"
)
sys.path.insert(0, str(_KNOWLEDGE_DIR))
from validator import parse_incar, validate_incar  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Validate VASP INCAR")
    parser.add_argument("--input-file", "-f", required=True, help="Path to INCAR file")
    parser.add_argument(
        "--task-type",
        "-t",
        default="scf",
        help="scf, relax, band, dos, md, hybrid, gw, phonon, neb, optical",
    )
    parser.add_argument("--is-metal", action="store_true", help="System is metallic")
    parser.add_argument(
        "--enmax", type=float, default=0.0, help="Max ENMAX from POTCAR (eV)"
    )
    args = parser.parse_args()

    incar_path = Path(args.input_file)
    if not incar_path.exists():
        print(f"File not found: {incar_path}", file=sys.stderr)
        sys.exit(1)

    text = incar_path.read_text(encoding="utf-8", errors="replace")
    tags = parse_incar(text)
    errors, warnings = validate_incar(
        tags,
        task_type=args.task_type,
        system_info={"is_metal": args.is_metal, "enmax": args.enmax},
    )

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("✓ Validation passed.")

    print(f"\nParsed {len(tags)} tags: {', '.join(sorted(tags.keys()))}")
    sys.exit(0)


if __name__ == "__main__":
    main()
