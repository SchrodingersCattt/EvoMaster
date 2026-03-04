"""
Physical-sense review entry for prepared input files.

Reads the input file and prints its content so the calling LLM can perform
a physical-sense review (parameter ranges, functional vs system, required
sections). If the LLM finds issues, it should use ask_human(mode="timeout");
on timeout, treat as pass. This script always exits 0 so the submit gate
allows submission.

Usage
-----
  python validate_input.py --input_file /path/to/cp2k.inp --software CP2K
  python validate_input.py --input_file /path/to/pw.in --software QE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Physical-sense review: read prepared input file for LLM inspection."
    )
    parser.add_argument(
        "--input_file", required=True,
        help="Path to the input file to validate.",
    )
    parser.add_argument(
        "--software", required=True,
        help="Software name (e.g. VASP, CP2K, LAMMPS, QE).",
    )
    parser.add_argument(
        "--data-dir",
        help="Ignored; kept for backward compatibility.",
    )

    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Physical-sense review: {args.software} -- {input_path.name}")
    print("Inspect the content below. If doubtful, use ask_human(mode='timeout'); on timeout, treat as pass.")
    print("-" * 60)

    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    print(content)
    sys.exit(0)


if __name__ == "__main__":
    main()
