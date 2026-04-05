#!/usr/bin/env python3
"""
Thin wrapper for the mat_master polyFF topology generator.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    target = skill_root.parent / "polyFF" / "scripts" / "generate_gmx_top.py"
    gaff_dat = skill_root.parent / "polyFF" / "assets" / "gaff_min.dat"
    if "--gaff-dat" not in sys.argv[1:]:
        sys.argv.extend(["--gaff-dat", str(gaff_dat)])
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
