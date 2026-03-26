#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    target = (
        Path(__file__).resolve().parents[4]
        / "poly-master"
        / "skills"
        / "poly-generator"
        / "scripts"
        / "build_polymer.py"
    )
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
