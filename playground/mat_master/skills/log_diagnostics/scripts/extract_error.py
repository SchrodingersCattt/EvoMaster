"""
Log Diagnostics Script: extract error code from VASP/LAMMPS logs.

Usage:
  python extract_error.py <log_file_path>

Output: single line with canonical error code (e.g. scf_diverged, kpoints_error)
for mapping to config.mat_master.resilient_calc.error_handlers.
"""

import re
import sys


def analyze_vasp_log(filepath: str) -> str:
    """Analyze VASP OUTCAR / stderr for known error patterns.

    Returns a canonical code for config-driven fix (e.g. scf_diverged).
    """
    errors = []
    try:
        with open(filepath, "r", errors="ignore") as f:
            for line in f:
                if "ZHEGV" in line or "ZPOTRF" in line:
                    return "scf_diagonalization_error"
                if "EDDDAV" in line or "SCF not converging" in line:
                    return "scf_diverged"
                if "IBZKPT" in line or "k-points" in line.lower() and "error" in line.lower():
                    return "kpoints_error"
                if "Grid too coarse" in line or "GRID" in line and "coarse" in line:
                    return "grid_too_coarse"
                if "Bond atom missing" in line:
                    return "bond_atom_missing"
    except OSError:
        return "io_error"
    return "unknown_error"


def analyze_lammps_log(filepath: str) -> str:
    """Analyze LAMMPS log for common failures. Stub; extend as needed."""
    try:
        with open(filepath, "r", errors="ignore") as f:
            content = f.read()
        if "ERROR:" in content:
            if "Lost atoms" in content:
                return "lost_atoms"
            if "Out of range" in content:
                return "out_of_range"
            return "lammps_error"
    except OSError:
        return "io_error"
    return "unknown_error"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python extract_error.py <log_file>", file=sys.stderr)
        sys.exit(1)
    log_path = sys.argv[1]
    if "OUTCAR" in log_path or "vasp" in log_path.lower() or log_path.endswith(".out"):
        code = analyze_vasp_log(log_path)
    else:
        code = analyze_lammps_log(log_path)
    print(code)


if __name__ == "__main__":
    main()
