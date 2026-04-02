#!/usr/bin/env python3
"""
Run VASPKIT in command-line mode for a single task.

Must be run in a directory that contains the required input files
(POSCAR, EIGENVAL, OUTCAR, etc.) for the chosen task.

Usage:
  python run_vaspkit.py --task 303 --symprec 1E-5
  python run_vaspkit.py --task 211
  python run_vaspkit.py --task 262

Output: stdout/stderr from vaspkit and exit code.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_KNOWN_VASPKIT_PATHS = [
    "/home/vaspkit.1.5.1.linux.x64/bin/vaspkit",
    "/opt/vaspkit/bin/vaspkit",
    "/usr/local/bin/vaspkit",
]


def _find_vaspkit() -> str | None:
    """Locate vaspkit binary: PATH first, then well-known install locations."""
    found = shutil.which("vaspkit")
    if found:
        return found
    for p in _KNOWN_VASPKIT_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run VASPKIT post-processing task in current directory."
    )
    parser.add_argument(
        "--task",
        type=int,
        required=True,
        help="VASPKIT task number (e.g. 303 K-path, 211 band, 262 Fermi surface)",
    )
    parser.add_argument(
        "--symprec",
        default="1E-5",
        help="Symmetry tolerance for symmetry-based tasks (default: 1E-5)",
    )
    parser.add_argument(
        "--timesym",
        type=int,
        default=None,
        choices=[0, 1],
        help="Time-reversal symmetry: 1=on, 0=off (for 302, 303, 251)",
    )
    args, extra = parser.parse_known_args()

    vaspkit_bin = _find_vaspkit()
    if not vaspkit_bin:
        print(
            "Error: vaspkit not found on PATH or known locations. "
            "Install vaspkit and ensure it is accessible.",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = [vaspkit_bin, "-task", str(args.task), "-symprec", args.symprec]
    if args.timesym is not None:
        cmd.extend(["-timesym", str(args.timesym)])
    if extra:
        cmd.extend(extra)

    cwd = Path.cwd()
    print(f"[vaspkit-postprocess] Working directory: {cwd}", file=sys.stderr)
    print(f"[vaspkit-postprocess] Command: {' '.join(cmd)}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=False,
            timeout=300,
        )
        print("-" * 60, file=sys.stderr)
        print(f"[vaspkit-postprocess] Exit code: {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    except subprocess.TimeoutExpired:
        print("Error: vaspkit timed out after 300s.", file=sys.stderr)
        sys.exit(124)
    except FileNotFoundError:
        print(f"Error: vaspkit not found: {vaspkit_bin}", file=sys.stderr)
        sys.exit(127)


if __name__ == "__main__":
    main()
