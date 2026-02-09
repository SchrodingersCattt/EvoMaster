"""
Parse calculation output files from open-source software (LAMMPS, etc.) and output a JSON summary.

Commercial software (VASP, Gaussian) is not supported. Use this for LAMMPS logs and other
supported open-source codes.

Usage:
  python parse_results.py --file log.lammps --type lammps

Output: JSON to stdout with potential_energy, temperature, pressure, etc.
"""

import argparse
import json
import re
import sys
from pathlib import Path


def parse_lammps(filepath: Path) -> dict:
    """Parse LAMMPS log for potential energy, temperature, pressure (when present)."""
    out = {"potential_energy": None, "temperature": None, "pressure": None, "step": None}
    try:
        with open(filepath, "r", errors="ignore") as f:
            for line in f:
                # Skip header lines
                if "Step" in line and "PotEng" in line:
                    continue
                # Data line: Step PotEng Temp [Press ...]
                if re.match(r"^\s*\d+\s+", line):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            out["step"] = int(parts[0])
                            out["potential_energy"] = float(parts[1])
                        except (ValueError, IndexError):
                            pass
                    if len(parts) >= 3:
                        try:
                            out["temperature"] = float(parts[2])
                        except (ValueError, IndexError):
                            pass
                    if len(parts) >= 4:
                        try:
                            out["pressure"] = float(parts[3])
                        except (ValueError, IndexError):
                            pass
        return out
    except OSError as e:
        return {"error": str(e), **out}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Parse open-source calculation output to JSON (LAMMPS supported; commercial codes not supported)."
    )
    ap.add_argument("--file", required=True, help="Path to LAMMPS log or other supported output")
    ap.add_argument("--type", required=True, choices=["lammps"], help="Software type (open-source only)")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        print(json.dumps({"error": f"File not found: {path}"}), file=sys.stderr)
        sys.exit(1)
    result = parse_lammps(path)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
