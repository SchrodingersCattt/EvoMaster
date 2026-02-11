"""
List available reference templates for a given software, grouped by prefix.

Usage:
  python list_references.py [--software cp2k|orca|gaussian|psi4] [--all]

  --software X : list templates for one software only
  --all        : list all software (default if no --software given)

Output format (for agent consumption):
  SOFTWARE (N templates):
    task_*  (need method_ pair): task_band.inp, task_scf.inp, ...
    method_* (merge into task_): method_admm_pbe0.inp, ...
    standalone (use as-is):      gw_g0w0.inp, tddft.inp, ...

Naming convention:
  task_*   = task skeleton (WHAT to calculate), uses basic PBE. Pair with method_*.
  method_* = functional/method setup (HOW to calculate). Merge into a task_*.
  no prefix = complete standalone template. Use directly, no merging needed.
"""

import argparse
import sys
from pathlib import Path


def list_templates(refs_dir: Path, software: str | None = None) -> None:
    """List templates grouped by prefix for each software subdirectory."""
    if not refs_dir.exists():
        print(f"References directory not found: {refs_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect software dirs (or filter to one)
    sw_dirs = sorted(
        d for d in refs_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    if software:
        sw_dirs = [d for d in sw_dirs if d.name.lower() == software.lower()]
        if not sw_dirs:
            print(f"No references found for software '{software}'.", file=sys.stderr)
            print(f"Available: {', '.join(d.name for d in refs_dir.iterdir() if d.is_dir() and not d.name.startswith('_'))}", file=sys.stderr)
            sys.exit(1)

    for sw_dir in sw_dirs:
        files = sorted(
            f.name for f in sw_dir.iterdir()
            if f.is_file() and not f.name.startswith("_")
        )
        if not files:
            continue

        task = [f for f in files if f.startswith("task_")]
        method = [f for f in files if f.startswith("method_")]
        standalone = [f for f in files if not f.startswith("task_") and not f.startswith("method_")]

        sw_name = sw_dir.name.upper()
        print(f"{sw_name} ({len(files)} templates):")

        if task:
            print(f"  task_*  (need method_ pair): {', '.join(task)}")
        if method:
            print(f"  method_* (merge into task_): {', '.join(method)}")
        if standalone:
            print(f"  standalone (use as-is):      {', '.join(standalone)}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="List reference templates")
    parser.add_argument("--software", "-s", type=str, default=None,
                        help="Filter to one software (e.g. cp2k, orca)")
    parser.add_argument("--all", action="store_true", default=False,
                        help="List all software (default)")
    args = parser.parse_args()

    refs_dir = Path(__file__).resolve().parent.parent / "references"
    list_templates(refs_dir, args.software)


if __name__ == "__main__":
    main()
